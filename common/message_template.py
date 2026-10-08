"""Message templates with {variables}, filled in for each contact. No UI, no database.

Supported variables: {nom}, {téléphone}, {détails} and {col1} … {col10} (the extra columns chosen when a contact file is
imported). Case and accents are ignored, so {Nom}, {telephone} and {DÉTAILS} work too."""
import re
import unicodedata

from common.sms_encoding import simplify_for_gsm7

VARIABLE_PATTERN = re.compile(r"\{([^{}]*)\}")

# canonical name (no accents, lowercase) -> label shown to the user
MAX_EXTRA_COLUMNS = 10
EXTRA_COLUMN_NAMES = [f"col{column_number}" for column_number in range(1, MAX_EXTRA_COLUMNS + 1)]
BASE_VARIABLE_LABELS = {
    "nom": "{nom}",
    "telephone": "{téléphone}",
    "details": "{détails}",
}
VARIABLE_LABELS = {**BASE_VARIABLE_LABELS, **{column_name: "{" + column_name + "}" for column_name in EXTRA_COLUMN_NAMES}}
POSSIBLE_VARIABLES_DESCRIPTION = ", ".join(BASE_VARIABLE_LABELS.values()) + f", {{col1}} à {{col{MAX_EXTRA_COLUMNS}}}"


def canonical_variable_name(raw_variable_name):
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", raw_variable_name)
        if not unicodedata.combining(character)
    )
    return without_accents.strip().casefold()


def find_unknown_variables(template_text):
    """Raw names (as typed) of variables that are not supported, without repeats."""
    unknown_names = []
    for raw_name in VARIABLE_PATTERN.findall(template_text):
        if canonical_variable_name(raw_name) not in VARIABLE_LABELS and raw_name not in unknown_names:
            unknown_names.append(raw_name)
    return unknown_names


def find_used_variables(template_text):
    """Canonical names of the supported variables used in the template."""
    used_names = {canonical_variable_name(raw_name) for raw_name in VARIABLE_PATTERN.findall(template_text)}
    return used_names & set(VARIABLE_LABELS)


def render_message(template_text, variable_values):
    """Replace every supported {variable}; unknown ones are left as typed."""
    def replace_one_variable(match):
        canonical_name = canonical_variable_name(match.group(1))
        return variable_values[canonical_name] if canonical_name in variable_values else match.group(0)

    return VARIABLE_PATTERN.sub(replace_one_variable, template_text)


def render_final_message_text(template_text, variable_values, simplify_to_7_bit=False):
    """The exact text of one SMS: template filled in, trimmed, and (if asked) kept within the 7-bit alphabet. Used both when a
    campaign is composed and when a batch is edited later, so both always produce the same text from the same variables."""
    message_text = render_message(template_text, variable_values).strip()
    return simplify_for_gsm7(message_text) if simplify_to_7_bit else message_text
