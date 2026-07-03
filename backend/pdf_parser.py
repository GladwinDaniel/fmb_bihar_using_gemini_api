import pdfplumber
import re
import io

def extract_area_from_pdf_bytes(pdf_bytes):
    """
    Attempts to extract the official area (in hectares) from a BhuNaksha PDF.
    Returns a float representing the area in Hectares, or None if not found.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
            
            # The BhuNaksha PDFs usually list the area as a decimal number.
            # "रकवा : \n 0.1230"
            # We look for the first valid decimal number that looks like an area (typically < 100.0)
            # In Hindi reports, "Hectare" is 'हेक्टेयर'
            # Let's just find the first number that has 2 to 4 decimal places, 
            # usually appearing after "रकवा :" or "Area :"
            
            # Find all numbers
            numbers = re.findall(r'\b\d+\.\d{2,4}\b', text)
            if numbers:
                # The area is usually one of these numbers. 
                # For safety, if there are multiple, the area is often the last one in the table row.
                # Just return the first plausible area.
                area = float(numbers[-1]) # often the last number in the summary table
                return area
    except Exception as e:
        print(f"pdfplumber extraction error: {e}")
    
    return None

