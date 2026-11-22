# Lab Inventory Tracker — Frontend

Frontend-only website for the Chanakya University Lab Inventory Tracker. No backend, database, credentials, or server code is included. The backend can be connected later using the API contract from your teammate.

## Run

1. Extract the ZIP and open the `lab_inventory_tracker_frontend` folder.
2. Open `index.html` in a browser or use VS Code Live Server.
3. Choose a **Preview role** to view Student, Lab Instructor, or Administrator screens.

Bootstrap and Bootstrap Icons load from CDNs, so styling and icons need an internet connection.

## Imported inventory

The project includes 24 lab records and 57 equipment records from the provided workbooks. The July report contributes summary-only lab records where no detailed inventory workbook was supplied. Detailed computer lab pages show system specifications and installed software. Workbook counts are displayed as **Inventory recorded**; they do not represent live availability or condition.

Repeated chemistry items listed under multiple experiments are shown once at the highest quantity listed, rather than being added across experiments. Phone numbers from the electronics workbook are not displayed.

## Items to verify

- The Engineering Chemistry workbook lists LA09, while the July report lists LA-01; its location is marked **Needs confirmation**.
- The chemistry conductivity inventory lists 6; the July report lists 5. The detailed workbook count is shown and the discrepancy is flagged.
- The Digital Electronics inventory shows 10 trainer kits, as confirmed by the user; the July report lists 8.
- The Analog Lab workbook does not list an instructor, so it remains **To be assigned**.

## Preview limitations

Sign-in and student registration are visual previews. Authentication, email verification, instructor invitations, and shared data are not connected. The preview does not store passwords. Inventory and edits are saved in this browser's `localStorage` and are not shared between devices.
