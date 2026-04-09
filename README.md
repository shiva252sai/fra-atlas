# 🏞️ FRA Atlas AI — Intelligent Forest Rights Monitoring & Decision Support System  
*Built for Smart India Hackathon 2025 | Team DevSphere*

> **Digitizing Forest Rights for Transparent, Data-Driven, and Mathematically Secure Governance.**
> FRA Atlas AI automates the verification of forest land rights, cross-references geospatial and satellite data, enforces blockchain-level cryptographic immutability, and powers a Decision Support System (DSS) to link FRA patta holders with government schemes.

---

## 🧠 The Problem
Legacy FRA claims, GIS maps, and Census records currently exist in isolated, non-digital fragments. Field staff perform document verification entirely manually, leaving massive gaps in accountability, spatial transparency, and the ability to track real-world satellite assets.

## 💡 The Architecture
FRA Atlas AI acts as a deeply integrated platform operating in three core pillars:
1. **Automated Digitation**: Unreadable paper scans are structured automatically via Tesseract OCR and Google Gemini's advanced LLM.
2. **Cryptographic Validation**: Every single document uploaded to the PostgreSQL database is cryptographically chained down to the `previous_hash`, ensuring absolute data integrity while preserving verifiable, audited editability.
3. **Spatial Intelligence**: Extracted coordinates query the **Google Earth Engine (GEE)** API to download live Sentinel-2 satellite imagery, which is parsed by a pre-trained **TensorFlow CNN (Convolutional Neural Network)** to permanently classify the land asset (e.g. Forest, AnnualCrop, River).

---

## 🧩 Core Features & Recent Integrations

### ⛓️ 1. Forensic Blockchain & Auditing System
To maintain trust while allowing for legitimate human typo-correction:
* **Strict Cryptographic Hashing**: Records are securely hashed sequentially.
* **Smart Edit Rippling**: Editing a document no longer breaks the system. The backend algorithm mathematically "ripples" the updated hash chronologically down the entire database.
* **Forensic Auditing (`fra_audit_logs`)**: Every single edit captures the user's ID/IP and saves a perfect **Before & After JSON snapshot** of the document.
* **Integrity Dashboard**: Map UI explicitly checks the mathematical security of the entire database in real-time, instantly surfacing malicious direct-database tampering with the exact ID and expected hash.

### 🗺️ 2. Advanced Geospatial Atlas (Map UI)
Upgraded React Leaflet visualizations for thousands of records:
* **Intelligent Clustering & Density Heatmaps**: Groups large collections of claims at high elevations and paints population distributions natively.
* **Village Coverage Zones**: Automatically projects approximate village perimeters by calculating the center-of-mass of all claimed coordinates within that jurisdiction.
* **Fallback Jitter & Gemini Geocoding**: When OpenStreetMap natively fails to map an obscure Indian village, the backend utilizes Gemini AI to estimate precise GPS coordinates. It then artificially scatters stacked pins (spatial jitter) so every claimant has a uniquely identifiable physical plot.

### 🛰️ 3. Automated Satellite Asset Extraction
Fully automated `GET /predict` GEE pipeline integrated directly into the `POST /confirm` document flow:
* Pulls real-time optical bands (B4, B3, B2) for the document's coordinates.
* Feeds pixel arrays into a `.h5` MobileNetV2 CNN classifier.
* Automatically records the `land_type`, `water_available`, and `irrigation` confidence into an immutable `asset_data` Postgres table for every FRA document.

---

## ⚙️ Tech Stack

### Frontend Application
* **Framework**: React.ts (Vite)
* **Design**: TailwindCSS, Shadcn UI, Lucide Icons
* **Geospatial Processing**: React Leaflet, Mapbox GL Elements
* **State Management**: React Hooks (useMemo, useMap)

### Backend Architecture
* **Core API**: FastAPI (Python)
* **Database Engine**: PostgreSQL + PostGIS Extension
* **Database Driver**: `psycopg2` `RealDictCursor`

### Artificial Intelligence & ML
* **Optical Character Recognition**: Tesseract / Google Vision
* **Geocoding & Data Validation Fallbacks**: Google Gemini (LangChain `ChatGoogleGenerativeAI`)
* **Computer Vision**: TensorFlow/Keras (`best_model.h5` MobileNet)
* **Satellite Indexing**: `earthengine-api` (Google Earth Engine)

---

## 🚀 Quick Start & Installation

### 1️⃣ Clone Repository
```bash
git clone https://github.com/jeetgoyal80/FRA-Portal.git
cd FRA-Portal
```

### 2️⃣ Configure Backend & Database
Make sure you have PostgreSQL running locally with the PostGIS extension enabled.
Create a `.env` file in the `Backend/` folder:
```env
DATABASE_URL=postgresql://user:password@localhost/fra.db
GEMINI_API_KEY=AIzaSy...
```
Install dependencies and run FastAPI:
```bash
cd Backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
earthengine authenticate  # (Required for the CNN satellite imagery processing)
uvicorn main:app --reload --port 8000
```

### 3️⃣ Launch Frontend
Open a new terminal.
```bash
cd Frontend
npm install
npm run dev
```
Navigate to `http://localhost:5173`.

---

## 📂 Folder Structure  

| **Folder** | **Description** |
|-------------|----------------|
| `Backend/` | FastAPI backend, OCR & ML services, API routes, database logic |
| `routers/` | REST endpoints for upload, DSS queries, and classification |
| `services/` | Scheme overlay service, OCR extraction, and ML inference |
| `Frontend/` | React dashboard for officers and citizens |
| `src/pages/` | Login, Dashboard, Atlas, AtlasEnhanced, DSS Query |
| `models/` | CNN model for land classification |
| `fra.db` | PostgreSQL database file (with PostGIS) |

---

## 🏁 Project Status  

| **Stage** | **Progress** |
|------------|-------------|
| **Forensic Audit History** | ✅ Fully Implemented |
| **Blockchain Integrity** | ✅ Fully Implemented |
| **Satellite Asset Extraction** | ✅ Fully Implemented (CNN) |
| **DSS Query Engine** | ⚙️ Under Development |
| **Frontend Map UI** | 🚀 Live with Enhanced Layers |

---

## 🧠 Model & Data References  

| **Resource** | **Link** |
|---------------|----------|
| **EuroSAT Sentinel-2 Dataset** | [https://github.com/phelber/EuroSAT](https://github.com/phelber/EuroSAT) |
| Deep Learning for Land Use & Cover Classification | [arXiv:1709.00029](https://arxiv.org/abs/1709.00029) |
| Forest Rights Act, 2006 | [IndiaCode PDF](https://www.indiacode.nic.in/bitstream/123456789/8311/1/a2007-02.pdf) |
| ISRO Bhuvan Portal | [https://bhuvan.nrsc.gov.in/ngmaps](https://bhuvan.nrsc.gov.in/ngmaps) |

---

## 👥 Team DevSphere  

| **Member** | **Responsibility** |
|-------------|--------------------|

© FRA Atlas AI Team | Smart India Hackathon 2025
