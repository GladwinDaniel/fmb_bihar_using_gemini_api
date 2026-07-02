# Frontend Architecture

This document outlines the frontend architecture of the Bihar Cadastral Map & Satellite Dashboard.

## Architecture Diagram

```mermaid
graph TD
    %% Browser Environment
    subgraph Client [Browser / Client Side]
        
        subgraph UI [User Interface]
            HTML[index.html <br> DOM Structure]
            CSS[style.css <br> Custom Styling]
            jQuery[jQuery <br> DOM Manipulation & AJAX]
            FontAwesome[FontAwesome <br> Icons]
        end

        subgraph MapEngine [GIS Map Engine]
            OpenLayers[OpenLayers v6]
            VectorLayers[Vector Layers <br> Parcels, Trees/Wells, Kurra Splits]
            TileLayers[Tile Layers <br> WMS, ArcGIS, Bihar GIS]
        end
        
        subgraph AppLogic [Application Logic app.js]
            State[State Management <br> currentParcel, offset]
            Init[Initialization & Dropdowns]
            Events[Event Listeners & UI Handlers]
            GISLogic[GIS Logic & Map Alignment]
            KurraLogic[Kurra Division Integration]
            APICalls[Backend API Integration]
        end
        
    end

    %% Backend & External Services
    subgraph Services [External & Backend Services]
        Flask[Flask Backend API & Proxies]
        ArcGIS[ArcGIS Basemap Services]
        BiharGIS[Bihar Govt GIS Services <br> Roads, Rivers]
        Geoserver[BhuNaksha GeoServer <br> via Proxy]
    end

    %% Relationships
    HTML --- CSS
    HTML --- FontAwesome
    HTML --- jQuery
    HTML --- OpenLayers
    HTML --- AppLogic

    Events --> jQuery
    Events --> GISLogic
    Events --> KurraLogic
    
    GISLogic --> OpenLayers
    KurraLogic --> OpenLayers
    Init --> OpenLayers
    Init --> APICalls
    
    OpenLayers --> VectorLayers
    OpenLayers --> TileLayers
    
    TileLayers -->|XYZ Tiles| ArcGIS
    TileLayers -->|ArcGISRest| BiharGIS
    TileLayers -->|WMS Image Load| Flask
    
    APICalls -->|AJAX POST/GET| Flask
    Flask -->|WMS Proxy| Geoserver

    classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef highlight fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
    class AppLogic,UI,MapEngine highlight;
```

## Component Breakdown

1. **User Interface (`index.html`, `style.css`)**: 
   - A responsive layout with a collapsible sidebar for controls and a main area for the map canvas.
   - External dependencies include jQuery for easy DOM manipulation and FontAwesome for icons.

2. **Map Engine (OpenLayers v6)**:
   - Handles the rendering of complex geospatial data.
   - **Tile Layers**: Fetches map tiles from ArcGIS (Satellite/Labels), Bihar Govt GIS (Highways, Rivers), and custom BhuNaksha WMS via a Flask proxy.
   - **Vector Layers**: Renders dynamic interactive elements like the selected parcel polygon, user-placed objects (trees, wells), and generated Kurra (subdivision) splits.

3. **Application Logic (`app.js`)**:
   - Acts as the controller connecting the UI to the Map Engine and Backend.
   - **State Management**: Maintains current map offsets for visual nudging (`offsetX`, `offsetY`), cached features (`osmFeaturesCache`), and current parcel details.
   - **Event Listeners**: Listens to UI clicks (dropdowns, sliders, map clicks) and triggers corresponding GIS or API actions.
   - **API Integration**: Uses `$.post` and `$.ajax` to communicate with the Flask backend to fetch administrative boundaries, query plots by GPS, and trigger complex subdivision calculations.
