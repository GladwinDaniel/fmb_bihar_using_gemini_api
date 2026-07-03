import math
import numpy as np
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import split
import shapely.affinity

def get_angle(p1, p2):
    return math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))

def split_polygon_by_ratio(poly, target_ratio, angle_deg):
    """
    Splits a polygon into two parts such that the first part has area = total_area * target_ratio.
    The splitting line is oriented at `angle_deg` (0 is horizontal).
    Uses a binary search approach to translate the split line across the polygon bounds.
    """
    target_area = poly.area * target_ratio
    
    # Translate poly to origin to avoid huge coordinate math errors
    cx_poly, cy_poly = poly.centroid.x, poly.centroid.y
    poly_shifted = shapely.affinity.translate(poly, xoff=-cx_poly, yoff=-cy_poly)
    
    # Get bounds to determine the length of our cutting line
    minx, miny, maxx, maxy = poly_shifted.bounds
    
    # Create a line long enough to cut the polygon entirely
    radius = math.hypot(maxx - minx, maxy - miny) * 2
    
    # The normal angle is what we move along. The cut line is perpendicular.
    # We want a cut line at angle_deg.
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * radius
    dy = math.sin(rad) * radius
    
    # Base cut line passing through origin (0,0) translated to center
    base_line = LineString([(-dx, -dy), (dx, dy)])
    
    # The direction perpendicular to the cut line is the axis along which we sweep
    sweep_rad = math.radians(angle_deg + 90)
    sweep_dx = math.cos(sweep_rad)
    sweep_dy = math.sin(sweep_rad)
    
    # Find the extents of the polygon along the sweep axis
    # Project all vertices of the polygon onto the sweep axis
    coords = list(poly_shifted.exterior.coords)
    projections = [c[0] * sweep_dx + c[1] * sweep_dy for c in coords]
    
    min_t = min(projections)
    max_t = max(projections)
    
    # Binary search for the split
    best_poly1 = None
    best_poly2 = None
    
    low = min_t
    high = max_t
    
    for _ in range(50): # 50 iterations should be enough for high precision
        mid = (low + high) / 2
        
        # Translate the base line such that its projection on the sweep axis is `mid`
        # We need a point that projects to `mid`
        px = mid * sweep_dx
        py = mid * sweep_dy
        
        # Create the cut line shifted to this point
        cut_line = shapely.affinity.translate(base_line, xoff=px, yoff=py)
        
        # Attempt to split
        try:
            geometry_collection = split(poly_shifted, cut_line)
        except Exception:
             # In case of robustness issues (e.g. invalid intersection)
            continue
            
        polygons = [geom for geom in geometry_collection.geoms if isinstance(geom, Polygon)]
        
        if len(polygons) < 2:
            # If it didn't split, we might be outside the valid range due to numerical issues
            # Or the line just touches the boundary
            if target_ratio < 0.5:
                high = mid
            else:
                low = mid
            continue
            
        # We need to determine which polygon is "below" or "left" of the line
        # A robust way is to just project the centroid of the polygons onto the sweep axis
        p1 = None
        p2 = None
        for p in polygons:
            c = p.centroid
            proj = c.x * sweep_dx + c.y * sweep_dy
            if proj < mid:
                if p1 is None:
                    p1 = p
                else:
                    p1 = p1.union(p)
            else:
                if p2 is None:
                    p2 = p
                else:
                    p2 = p2.union(p)
                    
        if p1 is None or p2 is None:
             # Fallback if the logic above fails (e.g. multiple pieces)
             polygons.sort(key=lambda p: p.centroid.x * sweep_dx + p.centroid.y * sweep_dy)
             p1 = polygons[0]
             p2 = polygons[-1]
             
        area1 = p1.area
        if area1 < target_area:
            low = mid
        else:
            high = mid
            best_poly1 = p1
            best_poly2 = p2
            
    if best_poly1 is None or best_poly2 is None:
        # Fallback if search failed, just return the original
        return [poly]
        
    # Translate back to original coordinates
    best_poly1 = shapely.affinity.translate(best_poly1, xoff=cx_poly, yoff=cy_poly)
    best_poly2 = shapely.affinity.translate(best_poly2, xoff=cx_poly, yoff=cy_poly)
        
    return [best_poly1, best_poly2]

def split_parcel(poly, shares, frontage_coords=None):
    """
    Splits a shapely Polygon into multiple parts according to the `shares` list (which must sum to ~1.0).
    If `frontage_coords` is provided, it attempts to align the split lines perpendicular to the frontage.
    """
    if not isinstance(poly, Polygon):
        raise ValueError("poly must be a shapely Polygon")
        
    total_shares = sum(shares)
    normalized_shares = [s / total_shares for s in shares]
    
    # Determine the angle for the cut lines
    angle_deg = 0
    if frontage_coords and len(frontage_coords) >= 2:
        # Simplistic approach: take the angle between the first and last point of the frontage
        # and add 90 degrees to cut perpendicular to it.
        p1 = frontage_coords[0]
        p2 = frontage_coords[-1]
        frontage_angle = get_angle(p1, p2)
        angle_deg = frontage_angle + 90
    else:
        # If no frontage, align to the longest axis of the minimum rotated rectangle
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        if len(coords) >= 3:
            d1 = math.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
            d2 = math.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1])
            if d1 > d2:
                # longest is p0 to p1. Cut perpendicular.
                angle_deg = get_angle(coords[0], coords[1]) + 90
            else:
                angle_deg = get_angle(coords[1], coords[2]) + 90
                
    result_polys = []
    current_poly = poly
    remaining_share = 1.0
    
    for i in range(len(normalized_shares) - 1):
        target_ratio = normalized_shares[i] / remaining_share
        
        parts = split_polygon_by_ratio(current_poly, target_ratio, angle_deg)
        if len(parts) >= 2:
            result_polys.append(parts[0])
            current_poly = parts[1]
        else:
            # Failed to split further
            break
            
        remaining_share -= normalized_shares[i]
        
    result_polys.append(current_poly)
    
    return result_polys

def generate_strategies(poly, shares, frontage_coords=None, river_frontage_coords=None):
    """
    Generates multiple splitting strategies.
    Returns a list of dicts: {"name": str, "polys": list of Polygons, "angle_deg": float}
    """
    if not isinstance(poly, Polygon):
        raise ValueError("poly must be a shapely Polygon")
        
    strategies = []
    
    # Base split logic using different angles
    total_shares = sum(shares)
    normalized_shares = [s / total_shares for s in shares]
    
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    d1 = math.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
    d2 = math.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1])
    
    longest_axis_angle = get_angle(coords[0], coords[1]) if d1 > d2 else get_angle(coords[1], coords[2])
    shortest_axis_angle = get_angle(coords[1], coords[2]) if d1 > d2 else get_angle(coords[0], coords[1])
    
    # Strategy 1: Parallel to shortest side (Compact Cut)
    # The cut line is parallel to the shortest side, meaning it is perpendicular to the longest side.
    # We pass the angle of the normal. If we want the cut line to be parallel to the shortest side,
    # its angle should be shortest_axis_angle. But split_parcel expects the angle of the CUT line?
    # Wait, split_polygon_by_ratio takes angle_deg as the angle of the cut line.
    
    def run_split(angle_deg):
        result_polys = []
        current_poly = poly
        remaining_share = 1.0
        
        for i in range(len(normalized_shares) - 1):
            target_ratio = normalized_shares[i] / remaining_share
            parts = split_polygon_by_ratio(current_poly, target_ratio, angle_deg)
            if not parts or len(parts) < 2:
                break
            # To ensure we keep splitting along the correct axis, we should sort parts by their projection
            # along the sweep axis. split_polygon_by_ratio returns them sorted.
            result_polys.append(parts[0])
            current_poly = parts[1]
            remaining_share -= normalized_shares[i]
        
        result_polys.append(current_poly)
        return result_polys

    # Strategy 1: Cut parallel to shortest side
    s1_polys = run_split(shortest_axis_angle)
    if len(s1_polys) == len(shares):
        strategies.append({"name": "Compact Cut (Parallel to Shortest Side)", "polys": s1_polys, "angle": shortest_axis_angle})
        
    # Strategy 2: Cut parallel to longest side
    s2_polys = run_split(longest_axis_angle)
    if len(s2_polys) == len(shares):
        strategies.append({"name": "Longitudinal Cut (Parallel to Longest Side)", "polys": s2_polys, "angle": longest_axis_angle})

    # Strategy 3: Perpendicular to Road (if road exists)
    if frontage_coords and len(frontage_coords) >= 2:
        p1 = frontage_coords[0]
        p2 = frontage_coords[-1]
        front_angle = get_angle(p1, p2)
        cut_angle = front_angle + 90
        s3_polys = run_split(cut_angle)
        if len(s3_polys) == len(shares):
            strategies.append({"name": "Road Access Cut (Perpendicular to Road)", "polys": s3_polys, "angle": cut_angle})
            
    # Strategy 4: Perpendicular to River (if river exists)
    if river_frontage_coords and len(river_frontage_coords) >= 2:
        p1 = river_frontage_coords[0]
        p2 = river_frontage_coords[-1]
        river_angle = get_angle(p1, p2)
        cut_angle = river_angle + 90
        s4_polys = run_split(cut_angle)
        if len(s4_polys) == len(shares):
            strategies.append({"name": "River Access Cut (Perpendicular to River)", "polys": s4_polys, "angle": cut_angle})
            
    # Remove duplicates based on angle similarity (modulo 180)
    unique_strategies = []
    seen_angles = []
    for s in strategies:
        angle_mod = s["angle"] % 180
        is_duplicate = False
        for sa in seen_angles:
            if abs(sa - angle_mod) < 5 or abs(sa - angle_mod) > 175:
                is_duplicate = True
                break
        if not is_duplicate:
            unique_strategies.append(s)
            seen_angles.append(angle_mod)

    return unique_strategies

