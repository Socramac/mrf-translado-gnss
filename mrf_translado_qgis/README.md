# MRF Translado GNSS

QGIS plugin for coordinate translation (translado) of GNSS data using PPP IBGE or SIGEF Memorial as reference.

---

## Overview

MRF Translado GNSS is a professional tool designed to automate the adjustment of georeferenced coordinates according to Brazilian INCRA/SIGEF standards.

The plugin allows users to import GNSS survey data, apply coordinate translation based on known reference points (PPP IBGE or SIGEF Memorial), and generate adjusted outputs directly within QGIS.

---

## Main Features

- Import GNSS points (TXT format)
- Import surveyed base coordinates
- Import **PPP IBGE reports**
- Import **SIGEF Memorial** with automatic DMS to UTM conversion
- Select base reference vertex
- Perform coordinate translation
- Generate vector layers in QGIS
- Export adjusted coordinates (TXT)
- Generate technical PDF report

---

## Target Users

This plugin is intended for:

- Surveying professionals
- Georeferencing specialists
- Rural property registration technicians
- GNSS and RTK users working under Brazilian standards (INCRA/SIGEF)

---

## Requirements

- QGIS 3.22 or higher
- No external dependencies required

---

## Installation

1. Open QGIS
2. Go to **Plugins → Manage and Install Plugins**
3. Install from ZIP or search after publication
4. Activate the plugin

---

## Usage

1. Import GNSS points
2. Import base coordinates
3. Import PPP IBGE or SIGEF Memorial
4. Select the reference base point
5. Run the coordinate translation
6. Export results or generate report

---

## Output

- Adjusted coordinate table
- QGIS layers (points and displacement vectors)
- Technical report in PDF format

---

## Author

Marcos Lopes  
MRF Consultoria

---

## Repository

https://github.com/Socramac/mrf-translado-gnss

---

## License

MIT License
