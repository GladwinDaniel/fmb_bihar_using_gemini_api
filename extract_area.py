import pdfplumber
import re
import sys

# Windows console encoding fix
sys.stdout.reconfigure(encoding='utf-8')

pdf_path = "e:/Fmb/static/reports/CS38010200861310600_35.pdf"

try:
    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        
        print("--- EXTRACTED TEXT ---")
        print(text[:2000])
        print("----------------------")
        
        # Area is often printed in numbers, e.g., "0.0100".
        # Let's use regex to find potential area formats like "0.\d+" or "[1-9]\d*\.\d+"
        numbers = re.findall(r'\b\d+\.\d+\b', text)
        print("Potential numbers in text:", numbers)
        
except Exception as e:
    print(f"Error: {e}")
