"""Read Excel/CSV files and turn rows into validated contacts. No UI, no database."""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from sms_text import normalize_senegal_number

EXCEL_EXTENSIONS = (".xlsx", ".xlsm")
CSV_EXTENSIONS = (".csv", ".txt")
CSV_DELIMITER_CANDIDATES = ",;\t|"
PHONE_HEADER_HINTS = ("telephone", "téléphone", "tel", "phone", "numero", "numéro", "mobile", "gsm", "portable", "contact")
NAME_HEADER_HINTS = ("nom", "name", "prenom", "prénom", "client", "full")
ROWS_SAMPLED_TO_GUESS_PHONE_COLUMN = 200


@dataclass
class ParsedContact:
    name: str
    phone_number: str
    custom_fields: dict = field(default_factory=dict)


@dataclass
class ImportPlan:
    valid_contacts: list = field(default_factory=list)
    invalid_rows: list = field(default_factory=list)   # (row number in the file, raw phone text)
    duplicate_count_in_file: int = 0
    empty_phone_count: int = 0


def cell_to_text(cell_value):
    """Excel stores 771234567 as a number: avoid '771234567.0'."""
    if cell_value is None:
        return ""
    if isinstance(cell_value, float) and cell_value.is_integer():
        return str(int(cell_value))
    return str(cell_value).strip()


def make_headers_readable_and_unique(raw_headers):
    seen_header_counts = {}
    readable_headers = []
    for column_index, raw_header in enumerate(raw_headers, start=1):
        header = raw_header or f"Colonne {column_index}"
        seen_header_counts[header] = seen_header_counts.get(header, 0) + 1
        if seen_header_counts[header] > 1:
            header = f"{header} ({seen_header_counts[header]})"
        readable_headers.append(header)
    return readable_headers


def read_excel_rows(file_path):
    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        return [[cell_to_text(cell_value) for cell_value in row] for row in workbook.active.iter_rows(values_only=True)]
    finally:
        workbook.close()


def read_csv_rows(file_path):
    for text_encoding in ("utf-8-sig", "latin-1"):  # Excel on Windows often saves CSV as latin-1
        try:
            file_text = Path(file_path).read_text(encoding=text_encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(file_text[:4096], delimiters=CSV_DELIMITER_CANDIDATES)
    except csv.Error:
        dialect = csv.excel
    return [[cell.strip() for cell in row] for row in csv.reader(file_text.splitlines(), dialect)]


def read_spreadsheet(file_path):
    """Return (headers, data_rows). Blank rows are dropped; every row is padded to the header width."""
    file_extension = Path(file_path).suffix.lower()
    if file_extension in EXCEL_EXTENSIONS:
        all_rows = read_excel_rows(file_path)
    elif file_extension in CSV_EXTENSIONS:
        all_rows = read_csv_rows(file_path)
    else:
        raise ValueError(f"Type de fichier non pris en charge « {file_extension} ». Utilisez .xlsx ou .csv (enregistrez d'abord les fichiers .xls en .xlsx).")

    non_blank_rows = [row for row in all_rows if any(cell for cell in row)]
    if not non_blank_rows:
        raise ValueError("Le fichier est vide.")
    column_count = max(len(row) for row in non_blank_rows)
    padded_rows = [row + [""] * (column_count - len(row)) for row in non_blank_rows]
    headers = make_headers_readable_and_unique(padded_rows[0])
    return headers, padded_rows[1:]


def find_column_by_header_hint(headers, header_hints, excluded_column_index=None):
    for column_index, header in enumerate(headers):
        if column_index == excluded_column_index:
            continue
        if any(hint in header.lower() for hint in header_hints):
            return column_index
    return None


def guess_phone_column(headers, data_rows):
    column_from_header = find_column_by_header_hint(headers, PHONE_HEADER_HINTS)
    if column_from_header is not None:
        return column_from_header
    sampled_rows = data_rows[:ROWS_SAMPLED_TO_GUESS_PHONE_COLUMN]
    best_column_index, best_valid_count = None, 0
    for column_index in range(len(headers)):
        valid_count = sum(1 for row in sampled_rows if normalize_senegal_number(row[column_index]))
        if valid_count > best_valid_count:
            best_column_index, best_valid_count = column_index, valid_count
    return best_column_index


def guess_name_column(headers, phone_column_index):
    return find_column_by_header_hint(headers, NAME_HEADER_HINTS, excluded_column_index=phone_column_index)


def build_import_plan(headers, data_rows, phone_column_index, name_column_index=None):
    import_plan = ImportPlan()
    phone_numbers_already_seen = set()
    for row_position, row in enumerate(data_rows):
        row_number_in_file = row_position + 2  # the header is row 1
        raw_phone_text = row[phone_column_index]
        if not raw_phone_text:
            import_plan.empty_phone_count += 1
            continue
        normalized_phone_number = normalize_senegal_number(raw_phone_text)
        if normalized_phone_number is None:
            import_plan.invalid_rows.append((row_number_in_file, raw_phone_text))
            continue
        if normalized_phone_number in phone_numbers_already_seen:
            import_plan.duplicate_count_in_file += 1
            continue
        phone_numbers_already_seen.add(normalized_phone_number)
        custom_fields = {
            headers[column_index]: cell_text
            for column_index, cell_text in enumerate(row)
            if cell_text and column_index not in (phone_column_index, name_column_index)
        }
        import_plan.valid_contacts.append(
            ParsedContact(
                name=row[name_column_index] if name_column_index is not None else "",
                phone_number=normalized_phone_number,
                custom_fields=custom_fields,
            )
        )
    return import_plan
