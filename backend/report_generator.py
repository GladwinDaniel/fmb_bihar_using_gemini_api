import os
import io
import tempfile
import cv2
import numpy as np
from fpdf import FPDF

def generate_kurra_report(plot_no, parcel_vertices, features, subdivisions, frontage_coords, parcel_info=None):
    # Defensive: ensure lists are never None
    parcel_vertices = parcel_vertices or []
    features = features or []
    subdivisions = subdivisions or []
    frontage_coords = frontage_coords or []

    # Guard against empty geometry (nothing to render)
    if not parcel_vertices:
        raise ValueError("parcel_vertices is empty  cannot generate report without parcel geometry")

    lats = [v[1] for v in parcel_vertices]
    lons = [v[0] for v in parcel_vertices]
    
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    # Add buffer
    lat_buffer = 0.0005
    lon_buffer = 0.0005
    
    # Create a white background instead of fetching satellite imagery
    img_w, img_h = 800, 600
    img_rgb = np.ones((img_h, img_w, 3), dtype=np.uint8) * 255
    
    tl_lat = max_lat + lat_buffer
    br_lat = min_lat - lat_buffer
    tl_lon = min_lon - lon_buffer
    br_lon = max_lon + lon_buffer
    
    def deg2pix(lat, lon):
        x = int((lon - tl_lon) / (br_lon - tl_lon) * img_w)
        y = int((tl_lat - lat) / (tl_lat - br_lat) * img_h)
        return x, y

    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    
    # Draw Subdivisions
    overlay = img_bgr.copy()
    colors = [(200, 100, 100), (100, 200, 100), (100, 100, 200), (200, 200, 100), (200, 100, 200), (100, 200, 200)]
    
    if subdivisions:
        for i, sub in enumerate(subdivisions):
            coords = sub['geometry']['coordinates'][0]
            pts = np.array([deg2pix(lat, lon) for lon, lat in coords], np.int32)
            pts = pts.reshape((-1, 1, 2))
            color = colors[i % len(colors)]
            cv2.fillPoly(overlay, [pts], color)
        
        cv2.addWeighted(overlay, 0.3, img_bgr, 0.7, 0, img_bgr)
        
        # Draw subdivision outlines
        for i, sub in enumerate(subdivisions):
            coords = sub['geometry']['coordinates'][0]
            pts = np.array([deg2pix(lat, lon) for lon, lat in coords], np.int32)
            pts = pts.reshape((-1, 1, 2))
            color = colors[i % len(colors)]
            cv2.polylines(img_bgr, [pts], isClosed=True, color=color, thickness=2)
    
    # Draw original parcel outline
    pts = np.array([deg2pix(lat, lon) for lon, lat in parcel_vertices], np.int32).reshape((-1, 1, 2))
    cv2.polylines(img_bgr, [pts], isClosed=True, color=(0, 0, 255), thickness=3)
    
    # Draw Road Frontage
    if frontage_coords:
        pts = np.array([deg2pix(lat, lon) for lon, lat in frontage_coords], np.int32).reshape((-1, 1, 2))
        cv2.polylines(img_bgr, [pts], isClosed=False, color=(0, 255, 255), thickness=4)
        
    # Draw Features
    for feat in features:
        x, y = deg2pix(feat['y'], feat['x']) # x is lon, y is lat
        if feat['type'] == 'tree':
            color = (0, 255, 0) # Green
        elif feat['type'] == 'well':
            color = (255, 0, 0) # Blue
        else:
            color = (0, 165, 255) # Orange
        cv2.circle(img_bgr, (x, y), 7, color, -1)
        cv2.circle(img_bgr, (x, y), 7, (255, 255, 255), 2)

    fd, temp_img_path = tempfile.mkstemp(suffix='.jpg')
    os.close(fd)
    cv2.imwrite(temp_img_path, img_bgr)
    
    # Generate PDF Document
    pdf = FPDF()
    pdf.add_page()
    
    # Header
    pdf.set_font("helvetica", "B", 16)
    pdf.cell(0, 10, f"Kurra Division Report: Plot {plot_no}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)
    
    # ---------------------------------------------------------
    # Section 1: Land Details & LPM Details
    # ---------------------------------------------------------
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "1. Land Details & LPM Details", new_x="LMARGIN", new_y="NEXT", border="B")
    pdf.ln(3)
    
    pdf.set_font("helvetica", "", 10)
    if parcel_info:
        pdf.cell(60, 6, f"District: {parcel_info.get('district', 'N/A')}")
        pdf.cell(60, 6, f"Sub-Division: {parcel_info.get('subdivision', 'N/A')}")
        pdf.cell(60, 6, f"Circle: {parcel_info.get('circle', 'N/A')}", new_x="LMARGIN", new_y="NEXT")
        
        pdf.cell(60, 6, f"Mouza: {parcel_info.get('mouza', 'N/A')}")
        pdf.cell(60, 6, f"Khata No: {parcel_info.get('khata_no', 'N/A')}")
        pdf.cell(60, 6, f"Plot No: {parcel_info.get('plot_no', plot_no)}", new_x="LMARGIN", new_y="NEXT")
        
        area = parcel_info.get('area', 0)
        pdf.cell(60, 6, f"Total Area: {area/4046.8564:.3f} acres ({area:.1f} sq.m)")
        pdf.cell(60, 6, f"Lat: {parcel_info.get('lat', 'N/A')}")
        pdf.cell(60, 6, f"Lon: {parcel_info.get('lon', 'N/A')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # ---------------------------------------------------------
    # Section 2: Dashboard Details
    # ---------------------------------------------------------
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "2. Dashboard Details", new_x="LMARGIN", new_y="NEXT", border="B")
    pdf.ln(3)
    
    pdf.set_font("helvetica", "", 10)
    if subdivisions:
        num_partitions = len(subdivisions)
        try:
            shares_str = ", ".join([f"{float(sub['properties']['share_percentage']):.1f}%" for sub in subdivisions])
        except (KeyError, TypeError, ValueError):
            shares_str = f"{num_partitions} parts"
        pdf.cell(0, 6, f"Requested Partitions: {num_partitions}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Requested Share Percentages: {shares_str}", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    # Visual Map Rendering
    pdf.image(temp_img_path, x=15, w=180)
    pdf.ln(5)
    pdf.set_font("helvetica", 'I', 9)
    pdf.cell(0, 6, "Legend: Red = Parcel Boundary, Yellow = Road Frontage, Green Dot = Tree, Blue Dot = Well", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # ---------------------------------------------------------
    # Section 3: Segregation Details & AI Summary
    # ---------------------------------------------------------
    pdf.add_page()
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "3. Segregation Details & Explainable AI Summary", new_x="LMARGIN", new_y="NEXT", border="B")
    pdf.ln(3)
    
    if subdivisions:
        pdf.set_font("helvetica", size=10)
        for i, sub in enumerate(subdivisions):
            props = sub.get('properties', {})
            sub_id = props.get('sub_plot_id', i + 1)
            share_pct = float(props.get('share_percentage', 0))
            area_sqm = float(props.get('area_sqm', 0))
            perimeter_m = float(props.get('perimeter_m', 0))
            frontage_m = float(props.get('frontage_m', 0))

            pdf.set_font("helvetica", 'B', 10)
            pdf.cell(0, 8, f"Sub-Plot {sub_id} ({share_pct:.1f}%)", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", size=10)
            pdf.cell(0, 6, f"  - Area: {area_sqm/4046.8564:.3f} acres ({area_sqm:.1f} sq.m)", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 6, f"  - Perimeter: {perimeter_m:.1f} m", new_x="LMARGIN", new_y="NEXT")
            if frontage_m > 0:
                pdf.cell(0, 6, f"  - Road Frontage Extent: {frontage_m:.1f} m", new_x="LMARGIN", new_y="NEXT")
                
            feats = props.get('contained_features', []) or []
            if feats:
                try:
                    feat_types = [f['type'].capitalize() for f in feats]
                    pdf.cell(0, 6, f"  - Features inside plot: {', '.join(feat_types)}", new_x="LMARGIN", new_y="NEXT")
                except Exception:
                    pdf.cell(0, 6, f"  - Features inside plot: {len(feats)} item(s)", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.cell(0, 6, "  - Features inside plot: None", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

    llm_explanation = parcel_info.get("llm_explanation", "") if parcel_info else ""
    strategy_name = parcel_info.get("strategy_name", "") if parcel_info else ""
    
    if llm_explanation and strategy_name:
        pdf.ln(5)
        pdf.set_font("helvetica", "B", 11)
        pdf.cell(0, 8, f"AI Strategy Recommendation: {strategy_name}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        
        pdf.set_font("helvetica", "", 10)
        pdf.multi_cell(0, 6, llm_explanation.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"'))
        pdf.ln(10)
        
        pdf.set_font("helvetica", "I", 9)
        pdf.multi_cell(0, 5, "Disclaimer: This AI summary was dynamically generated in a stateless session utilizing the local LLM running via LM Studio. The model analyzes mathematically precise boundaries and detected visual map context against the UP Revenue Code, 2006.")

    os.unlink(temp_img_path)
    return bytes(pdf.output())
