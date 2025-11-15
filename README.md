# receipt-expense-tracker
# Receipt Processing Pipeline (Google Drive → Google Sheets)

Simplify your personal expense tracking.  Just take a picture of your receipt from a Google folder and
all items will be automatically categorized and added to a Google spreadsheet.

A fully automated pipeline that:

1. Watches a specific Google Drive folder for receipts (PDF or images)
2. Extracts text using Google Cloud Vision
3. Uses OpenAI GPT to parse structured data (store, date, amount, line items, taxable flag)
4. Writes results to Google Sheets
5. Archives processed files
6. Logs each run

This project is designed for personal budgeting, receipt tracking, and data engineering practice.

---

## 🚀 Features

- OCR for PDFs + images  
- GPT-based structured extraction  
- Itemized + summary output  
- Spreadsheet with `Taxable` column  
- Configurable categories  
- Automatic logging  
- Extensible to SQLite or Postgres later

---

## 🧱 Tech Stack

- Python 3.10+
- Google Cloud Vision API
- Google Drive API
- Google Sheets API
- OpenAI API
- Pandas (optional)
- dotenv

---

## 📦 Installation

Clone the repo:

```bash
git clone https://github.com/yourusername/receipt-pipeline.git
cd receipt-pipeline


Ready for daily scheduled execution via cron/Task Scheduler

🧠 Example Output (Items Sheet)
Receipt ID	Store	Date	        Item	    Category	Price	Taxable	Price w/ Tax
RCT-A12F9	Target	2025-01-11	    Apples	    Groceries	3.99	No	    3.99
RCT-A12F9	Target	2025-01-11	    Shampoo	    Health	    7.49	Yes	    8.10