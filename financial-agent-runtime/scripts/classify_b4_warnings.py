from pathlib import Path

text = Path("b4_temporal_wait.out").read_text(encoding="utf-8", errors="replace")
stdout_part = text.split("---TEMPORAL-OUT---")[1].split("---TEMPORAL-ERR---")[0]

patterns = {
    "pydantic_deprecated": "PydanticDeprecatedSince211",
    "pytest_unraisable": "PytestUnraisableExceptionWarning",
    "deprecation_warning": "DeprecationWarning",
    "insecure_key_length": "InsecureKeyLengthWarning",
    "sqlalchemy_resource": "ResourceWarning",
    "sqlalchemy_sa_warning": "SAWarning",
}

for name, needle in patterns.items():
    print(f"{name}={stdout_part.count(needle)}")

warning_lines = [line for line in stdout_part.splitlines() if "warning" in line.lower()]
print(f"warning_lines={len(warning_lines)}")
