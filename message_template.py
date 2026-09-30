"""Message templates with {variables}, filled in for each contact. No UI, no database.

Supported variables for now: {nom}, {téléphone}, {détails} (case and accents are ignored, so
{Nom}, {telephone} and {DÉTAILS} work too). More columns can be added to VARIABLE_LABELS later."""
import re
import unicodedata

VARIABLE_PATTERN = re.compile(r"\{([^{}]*)\}")

# canonical name (no accents, lowercase) -> label shown to the user
VARIABLE_LABELS = {
    "nom": "{nom}",
    "telephone": "{téléphone}",
    "details": "{détails}",
}


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
