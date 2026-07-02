# Backend Architecture

This document outlines the backend architecture of the Bihar Cadastral Map & Satellite Dashboard.

## Architecture Diagram

```mermaid
graph TD
    %% Client / Gateway
    Client[Frontend / Client]
    
    %% Flask API Gateway
    subgraph FlaskApp [Flask Application: app.py]
        Router[API Router & Controllers]
        Cache[JSON File Cache <br> Dropdowns & Extents]
        SessionManager[Requests Session Manager <br> BhuNaksha Authentication]
    end

    %% Core Modules
    subgraph Modules [Business Logic Modules]
        Subdivide[subdivide.py <br> Geospatial Polygon Splitting using Shapely]
        LLM[llm_expert.py <br> AI Strategy Selection]
        Report[report_generator.py <br> PDF/Report Generation]
        CVDetector[cv_detector.py <br> Spatial Feature Querying]
        PDFParser[pdf_parser.py <br> Land Record Extraction]
    end

    %% Database Models
    subgraph Database [SQLite DB: models.py]
        Parcel[(Parcel)]
        ParcelVertex[(ParcelVertex)]
        BoundarySegment[(BoundarySegment)]
        LdmReport[(LdmReport)]
    end

    %% External Services
    subgraph External [External Services]
        BhuNaksha[BhuNaksha GIS & REST APIs]
        BiharGIS[Bihar Govt GIS Services]
        LLMService[LLM Service Provider]
    end

    %% Connections
    Client -->|HTTP Requests| Router
    
    %% Flask App Internal
    Router -->|Read/Write| Cache
    Router -->|Maintains Session| SessionManager
    SessionManager -->|Authenticated Requests| BhuNaksha
    
    %% DB Interaction
    Router <--> Database
    
    %% Module Interaction
    Router -->|Feature Request| CVDetector
    CVDetector -->|Query Spatial Data| BiharGIS
    
    Router -->|Parse Land Records| PDFParser
    
    Router -->|Subdivision Request| Subdivide
    Subdivide -->|Generate Multiple Split Strategies| LLM
    LLM -->|Prompt Evaluation| LLMService
    LLM -->|Select Optimal Strategy| Subdivide
    
    Router -->|Generate Division Report| Report

    classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef highlight fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
    classDef db fill:#f3e5f5,stroke:#8e24aa,stroke-width:2px;
    
    class FlaskApp,Modules highlight;
    class Database db;
```

## Component Breakdown

1. **API Gateway & App Runner (`app.py`)**:
   - Built on Flask, this acts as the central entry point.
   - **Proxies**: Safely forwards geospatial and data requests to the BhuNaksha servers while managing a persistent, authenticated `requests.Session`.
   - **Caching**: Uses simple local JSON files (`dropdown_cache.json`, `extent_cache.json`) to speed up redundant API calls.
   - **Controllers**: Handles data export (`export_geojson`, `export_csv`) and directs complex processes to specific modules.

2. **Database Layer (`models.py`)**:
   - Uses SQLAlchemy to manage an SQLite database (`bhunaksha.db`).
   - Stores parsed parcel geometries (`Parcel`, `ParcelVertex`), boundary constraints (`BoundarySegment`), and generated reports (`LdmReport`).

3. **Core Modules**:
   - **`subdivide.py`**: The geospatial engine for land division (Kurra). It uses the `shapely` library to calculate different ways to slice a polygon based on target ratios and frontage constraints.
   - **`llm_expert.py`**: Acts as a decision engine. Once `subdivide.py` generates multiple valid subdivision strategies, it passes them to an LLM, which evaluates physical constraints (like trees, wells, or river adjacency) to select the most practical division.
   - **`report_generator.py`**: Compiles the final subdivision decisions and parcel data into downloadable reports.
   - **`cv_detector.py`**: Interfaces with external GIS services to detect nearby infrastructure and topological features relevant to a plot.
   - **`pdf_parser.py`**: Extracts text and area metrics directly from uploaded or queried land record PDFs.
