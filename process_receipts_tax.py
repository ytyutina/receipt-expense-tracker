import os
import shutil
import time
import uuid
import json
import io
import re
from collections import defaultdict

from PIL import Image
from pdf2image import convert_from_path
import fitz  # PyMuPDF

from google.cloud import vision
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI, OpenAIError

from dotenv import load_dotenv
load_dotenv()

# CONFIGURATION
RECEIPT_FOLDER = os.getenv("RECEIPT_FOLDER")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ITEMS_RANGE = "Items!A:H"
CATEGORY_LIST = [
    "Groceries", "Dining Out", "Transportation", "Housing", "Utilities",
    "Health", "Entertainment", "Clothing", "Travel", "Gifts/Charity",
    "Other", "General Merchandise", "Auto repair"
]

# CLIENTS
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_FILE
openai_api_key = os.getenv("OPENAI_API_KEY")
client_vision = vision.ImageAnnotatorClient()
client_openai = OpenAI()

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
service_sheets = build("sheets", "v4", credentials=creds)

# UTILITIES
def generate_receipt_id():
    return f"RCT-{str(uuid.uuid4())[:6].upper()}"

def is_image_file(filename):
    return filename.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp", ".pdf"))

def is_pdf_text_based(pdf_path):
    with fitz.open(pdf_path) as doc:
        return any(page.get_text().strip() for page in doc)

def extract_text_from_image(image_path):
    if image_path.lower().endswith(".pdf"):
        if is_pdf_text_based(image_path):
            with fitz.open(image_path) as doc:
                return "\n".join(page.get_text() for page in doc)
        else:
            images = convert_from_path(image_path)
            texts = []
            for img in images:
                byte_io = io.BytesIO()
                img.save(byte_io, format='JPEG')
                image = vision.Image(content=byte_io.getvalue())
                response = client_vision.text_detection(image=image)
                if response.text_annotations:
                    texts.append(response.text_annotations[0].description)
            return "\n".join(texts)
    else:
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        response = client_vision.text_detection(image=image)
        return response.text_annotations[0].description if response.text_annotations else ""

# --- GPT PARSING WITH MODEL FALLBACK ---
def parse_receipt_with_gpt(ocr_text, log_file=None):
    models_to_try = ["gpt-4-turbo", "gpt-3.5-turbo"]
    categories_str = ", ".join(CATEGORY_LIST)

    prompt = f"""
You are a precise data-extraction assistant. From the receipt text below, extract:
- Store name, convert store name to camel case
- Purchase date (YYYY-MM-DD if present)
- Total amount (numeric)
- Tax total (numeric, if present; otherwise 0)
- An itemized list of each item with:
    - Item name
    - Category (choose ONE from: {categories_str}; use 'Other' if none match).  Assign alcohol to Entertainment.
    - Price (numeric, pre-tax if the receipt separates tax)
    - Taxable (true or false) — decide based on markings or context in the receipt text

Return ONLY valid JSON (no commentary) in this format:

{{
  "Store name": "...",
  "Purchase date": "YYYY-MM-DD",
  "Total amount": 123.45,
  "Tax total": 1.23,
  "Items": [
    {{"Item": "Bananas", "Category": "Groceries", "Price": 5.00, "Taxable": false}},
    {{"Item": "Shampoo", "Category": "Health", "Price": 9.00, "Taxable": true}}
  ]
}}

Receipt text:
\"\"\"
{ocr_text}
\"\"\"
"""
    last_err = None
    for model_name in models_to_try:
        try:
            response = client_openai.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=1200,
            )
            content = response.choices[0].message.content

            # Extract JSON substring if extra text exists
            json_str = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_str:
                raise ValueError("No JSON object found in response")
            parsed = json.loads(json_str.group(0))

            if log_file:
                log_message(log_file, f"🧠 Parsed using {model_name}")
            return parsed

        except (OpenAIError, json.JSONDecodeError, ValueError) as e:
            last_err = e
            continue

    raise RuntimeError(f"All model attempts failed. Last error: {last_err}")

# --- GOOGLE SHEETS ---
def append_to_google_sheets(receipt_data, receipt_id):
    # Prepare rows for item-level data
    item_rows = []
    tax_total = receipt_data.get("Tax total", 0.0)
    store = receipt_data.get("Store name", "")
    date = receipt_data.get("Purchase date", "")
    total = receipt_data.get("Total amount", 0.0)

    # Determine taxable subtotal to distribute tax proportionally later
    taxable_items = [i for i in receipt_data["Items"] if i.get("Taxable")]
    taxable_subtotal = sum(i["Price"] for i in taxable_items) or 1.0

    for item in receipt_data["Items"]:
        price = float(item["Price"])
        taxable = bool(item.get("Taxable", False))
        # distribute tax proportionally to taxable items
        tax_share = (price / taxable_subtotal) * tax_total if taxable else 0
        price_w_tax = round(price + tax_share, 2)

        item_rows.append([
            receipt_id,
            store,
            date,
            item["Item"],
            item["Category"],
            price,
            taxable,
            price_w_tax
        ])

    service_sheets.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=ITEMS_RANGE,
        valueInputOption="USER_ENTERED",
        body={"values": item_rows}
    ).execute()

def log_message(log_file, message):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    with open(log_file, "a") as f:
        f.write(f"{timestamp} {message}\n")

# --- MAIN PIPELINE ---
def process_all_receipts():
    archive_folder = os.path.join(RECEIPT_FOLDER, "archive")
    error_folder = os.path.join(RECEIPT_FOLDER, "errors")
    log_file = os.path.join(RECEIPT_FOLDER, "run_log.txt")
    os.makedirs(archive_folder, exist_ok=True)
    os.makedirs(error_folder, exist_ok=True)

    for filename in os.listdir(RECEIPT_FOLDER):
        filepath = os.path.join(RECEIPT_FOLDER, filename)
        if not os.path.isfile(filepath) or filename in ("run_log.txt",):
            continue
        if not is_image_file(filename):
            continue

        try:
            print(f"Processing: {filename}")
            ocr_text = extract_text_from_image(filepath)
            receipt_data = parse_receipt_with_gpt(ocr_text, log_file)
            receipt_id = generate_receipt_id()
            append_to_google_sheets(receipt_data, receipt_id)
            shutil.move(filepath, os.path.join(archive_folder, filename))
            log_message(log_file, f"✅ Processed and archived: {filename} as {receipt_id}")
        except Exception as e:
            shutil.move(filepath, os.path.join(error_folder, filename))
            log_message(log_file, f"❌ Failed to process {filename}: {e}")
            print(f"Error processing {filename}: {e}")

if __name__ == '__main__':
    process_all_receipts()
