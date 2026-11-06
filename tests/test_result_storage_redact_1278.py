"""#1278: redacting secrets in published code must keep it valid code and still hide real literals."""
import pytest

from labhq.portfolio import clean_publication_text, code_syntax_issues, scan_publish_files
from labhq.settings import Settings

REQ = {"id": "r", "text": ""}
NAME = "steps/s4_qc/outputs/scripts/qc_check.py"


def _clean(body, name=NAME):
    return clean_publication_text(name, body, Settings(), REQ)


def test_the_8th_trial_line_stays_valid_python():
    code = 'token = r["fixed_input_value"].split(",")[0].strip()\nprint(token)\n'
    cleaned = _clean(code)
    assert cleaned == code, "an expression is not a secret value"
    assert code_syntax_issues({NAME: cleaned}) == []
    assert scan_publish_files({NAME: cleaned}) == []


@pytest.mark.parametrize("line", [
    'api_key = os.environ["API_KEY"]',
    "password = getpass.getpass()",
    "secret = load_secret(path)",
    "token = tokens[0]",
    "token = row.token.strip()",
    'password = ""',
])
def test_names_that_are_not_literals_are_left_alone(line):
    cleaned = _clean(f"import os\n{line}\n")
    assert line in cleaned
    assert code_syntax_issues({NAME: cleaned}) == []
    assert scan_publish_files({NAME: cleaned}) == []


@pytest.mark.parametrize("line,leaked", [
    ('api_key = "sk-abc123def456ghi789"', "sk-abc123def456ghi789"),
    ("password = 'hunter2xyz'", "hunter2xyz"),
    ('headers = {"token": "abcdef123456"}', "abcdef123456"),
    ("secret = r'abc123xyz789'", "abc123xyz789"),
    ("token = 12345678", "12345678"),
])
def test_real_literals_are_still_hidden_and_the_code_still_compiles(line, leaked):
    cleaned = _clean(f"{line}\n")
    assert leaked not in cleaned
    assert "redacted-secret" in cleaned
    assert code_syntax_issues({NAME: cleaned}) == []
    assert scan_publish_files({NAME: cleaned}) == []


def test_shell_and_r_literals_are_hidden_without_breaking_the_script():
    sh = _clean('export TOKEN=abc123xyz789\ncurl -H "$TOKEN" x\n', "steps/s1/outputs/scripts/a.sh")
    assert "abc123xyz789" not in sh and '"$TOKEN"' in sh
    r = _clean('token <- "abc123xyz789"\nx <- token\n', "steps/s1/outputs/scripts/a.R")
    assert "abc123xyz789" not in r


def test_an_annotated_assignment_keeps_its_annotation_and_hides_the_literal():
    cleaned = _clean('token: str = "abc123xyz789"\nvalue: int = 3\n')
    assert "abc123xyz789" not in cleaned and "token: str = " in cleaned
    assert code_syntax_issues({NAME: cleaned}) == []


def test_already_redacted_code_is_not_flagged_and_prose_keeps_its_rule():
    assert scan_publish_files({NAME: 'token = "<redacted-secret>"\n'}) == []
    assert scan_publish_files({"README.md": 'token = "<redacted-secret>"\n'}) == []
    assert scan_publish_files({"README.md": "token = abc123xyz789\n"}) == [{"kind": "secret", "path": "README.md"}]
    assert scan_publish_files({NAME: 'API_KEY = "abc123xyz789"\n'}) == [{"kind": "secret", "path": NAME}]


@pytest.mark.parametrize("line,leaked", [
    ('token = """hunter2xyz"""', "hunter2xyz"),
    ("password = '''line1\nhunter2xyz'''", "hunter2xyz"),
    ('token = ("hunter2xyz")', "hunter2xyz"),
    ('token = ("part1abc" "part2xyz")', "part2xyz"),
    ('secret = "part1abc" + "part2xyz"', "part2xyz"),
    ("password = -12345678", "12345678"),
    ("secret = 0x123456789abcdef", "123456789abcdef"),
    ("token = 1_234_567_890", "234_567"),
])
def test_every_python_literal_form_is_hidden(line, leaked):
    cleaned = _clean(f"{line}\n")
    assert leaked not in cleaned and "redacted-secret" in cleaned
    assert code_syntax_issues({NAME: cleaned}) == []
    assert scan_publish_files({NAME: cleaned}) == []
    assert scan_publish_files({NAME: line + "\n"}) == [{"kind": "secret", "path": NAME}]


def test_a_number_inside_an_expression_is_not_a_literal_secret():
    code = "token = 12345678 + offset\n"
    assert _clean(code) == code


def test_a_string_on_the_next_line_is_not_joined_to_the_secret():
    code = 'token = "hunter2xyz"\n"""docstring stays"""\n'
    cleaned = _clean(code)
    assert "hunter2xyz" not in cleaned and '"""docstring stays"""' in cleaned


@pytest.mark.parametrize("line,leaked", [
    ('token = "hunter2\\nxyz"', "xyz"),
    ('token: Annotated[str, Field(repr=False)] = "hunter2xyz"', "hunter2xyz"),
    ('token = ("part1abc"\n         "part2xyz")', "part2xyz"),
    ('client = make(api_key="hunter2xyz")', "hunter2xyz"),
    ('self.password = b"hunter2xyz"', "hunter2xyz"),
    ('config["secret"] = f"hunter2xyz{n}"', "hunter2xyz"),
    ('token = "abc123xyz" + suffix', "abc123xyz"),
])
def test_python_secrets_are_found_by_the_parser_in_every_form(line, leaked):
    cleaned = _clean(f"{line}\n")
    assert leaked not in cleaned and "redacted-secret" in cleaned
    assert code_syntax_issues({NAME: cleaned}) == []
    assert scan_publish_files({NAME: cleaned}) == []
    assert scan_publish_files({NAME: line + "\n"}) == [{"kind": "secret", "path": NAME}]


def test_shell_ansi_c_and_adjacent_quotes_are_hidden():
    for line in ("TOKEN=$'hunter2xyz'", 'TOKEN="part1abc""part2xyz"', "export PASSWORD='hunter2xyz'"):
        cleaned = _clean(line + "\n", "steps/s1/outputs/scripts/a.sh")
        assert "hunter2xyz" not in cleaned and "part2xyz" not in cleaned, line


def test_shell_values_with_equals_are_whole_and_a_comment_is_not_a_value():
    sh = "steps/s1/outputs/scripts/a.sh"
    assert "xyz789" not in _clean("TOKEN=abc123=xyz789\n", sh)
    comment = "TOKEN= # populated by caller\n"
    assert _clean(comment, sh) == comment


@pytest.mark.parametrize("line", [
    'def connect(host, token="hunter2xyz"):\n    return host',
    'def connect(*, password="hunter2xyz"):\n    return 1',
    'handler = lambda secret="hunter2xyz": secret',
])
def test_a_secret_default_of_a_function_parameter_is_hidden(line):
    cleaned = _clean(line + "\n")
    assert "hunter2xyz" not in cleaned and code_syntax_issues({NAME: cleaned}) == []


SH = "steps/s1/outputs/scripts/a.sh"
R = "steps/s1/outputs/scripts/a.R"


@pytest.mark.parametrize("line", [
    "TOKEN=abc'xyz'",
    "TOKEN=abc\"xyz\"",
    "TOKEN=\"abc\"'xyz'",
    "TOKEN=prefix-$OTHER",
    'TOKEN="prefix-$OTHER"',
    "TOKEN=${OTHER:-hunter2xyz}",
    "TOKEN=$((1+2))",
    "TOKEN=(hunter2xyz)",
    r"TOKEN=hunter2\ xyz",
    "export PASSWORD=hunter2xyz",
    "declare -x SECRET='hunter2xyz'",
    "api_key=hunter2xyz",
])
def test_shell_values_are_hidden_unless_surely_safe(line):
    cleaned = _clean(f"{line}\n", SH)
    assert "hunter2" not in cleaned and "xyz'" not in cleaned and "xyz\"" not in cleaned
    assert "redacted-secret" in cleaned
    assert scan_publish_files({SH: cleaned}) == []
    assert scan_publish_files({SH: line + "\n"}) == [{"kind": "secret", "path": SH}]


@pytest.mark.parametrize("line", [
    "TOKEN=$OTHER", "TOKEN=${OTHER}", 'TOKEN="$OTHER"', 'TOKEN="${OTHER}"', "TOKEN=$(cat /run/secret)",
    'TOKEN="$(pass show x)"', "TOKEN=`cat file`", "TOKEN=", "TOKEN= # filled by the caller", "TOKEN=$1",
    'TOKEN="<redacted-secret>"',
])
def test_shell_values_that_are_surely_safe_are_left_alone(line):
    code = f"{line}\necho done\n"
    assert _clean(code, SH) == code
    assert scan_publish_files({SH: code}) == []


@pytest.mark.parametrize("line", [
    'token <- "hunter2xyz"', "password = 'hunter2xyz'", "secret <<- 123456789", 'api_key <- r"(hunter2xyz)"',
    'config <- list(token = "hunter2xyz")', 'token <- paste0("hunter2", "xyz")', 'token <- c("hunter2xyz")',
])
def test_r_values_are_hidden_unless_surely_safe(line):
    cleaned = _clean(f"{line}\n", R)
    assert "hunter2xyz" not in cleaned and "123456789" not in cleaned and 'hunter2", "xyz' not in cleaned
    assert "redacted-secret" in cleaned
    assert scan_publish_files({R: cleaned}) == []
    assert scan_publish_files({R: line + "\n"}) == [{"kind": "secret", "path": R}]


@pytest.mark.parametrize("line", [
    'token <- Sys.getenv("API_TOKEN")', "password <- readline()", "secret <- other_variable", "token <- cfg$token",
    "token <- fetch_token(user)", "token <- NULL", "x <- token", 'if (token == "abc") 1',
    'token = keyring::key_get("svc")',
])
def test_r_values_that_are_surely_safe_are_left_alone(line):
    code = f"{line}\nprint(1)\n"
    assert _clean(code, R) == code
    assert scan_publish_files({R: code}) == []


def test_a_parameter_expansion_is_not_read_as_an_assignment():
    code = 'echo "${TOKEN:-fallback}" "${PASSWORD:=x}"\nexport PATH=$PATH:/bin\n'
    assert _clean(code, SH) == code


def test_arithmetic_in_double_quotes_is_a_literal_and_file_path_is_not_a_lookup():
    assert "123456789" not in _clean('TOKEN="$((123456789))"\n', SH)
    assert "hunter2" not in _clean('token <- file.path("hunter2", "xyz")\n', R)


def test_an_assignment_shaped_text_inside_a_string_keeps_its_quotes():
    assert _clean('print("token = example")\n', R) == 'print("token = example")\n'
    cleaned = _clean('message <- "password = hunter2xyz and more"\nprint(1)\n', R)
    assert "hunter2xyz" not in cleaned and cleaned.count('"') == 2 and "redacted-secret" in cleaned
    cleaned = _clean('echo "TOKEN=hunter2xyz done"\n', SH)
    assert "hunter2xyz" not in cleaned and cleaned.startswith('echo "TOKEN=') and cleaned.count('"') == 2
    assert _clean('# token = abc123xyz\nx <- 1\n', R) == '# token = abc123xyz\nx <- 1\n'


def test_namespaced_base_lookups_are_still_lookups():
    code = 'token <- base::Sys.getenv("API_TOKEN")\npassword <- utils::readline()\n'
    assert _clean(code, R) == code
