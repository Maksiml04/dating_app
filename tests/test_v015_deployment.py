from app.deployment_check import parse_env, validate

def good():
    return {"POSTGRES_PASSWORD": "a_very_long_secret", "DATABASE_URL": "postgresql+psycopg://dateideas:a_very_long_secret@db:5432/dateideas", "MINIO_ROOT_PASSWORD": "secure-secret", "ADMIN_API_KEY": "secure-api-key", "TELEGRAM_BOT_TOKEN": "123:valid", "TELEGRAM_WEBHOOK_SECRET": "webhook-secret"}

def test_good_config():
    assert validate(good()) == []

def test_password_mismatch():
    config = good(); config["POSTGRES_PASSWORD"] = "different"
    assert any("does not match" in err for err in validate(config))

def test_missing_secret():
    config = good(); config["ADMIN_API_KEY"] = "replace-me"
    assert any("ADMIN_API_KEY" in err for err in validate(config))

def test_admin_session():
    config = good(); config["ADMIN_PASSWORD"] = "some-password"
    assert any("ADMIN_SESSION_SECRET" in err for err in validate(config))

def test_telethon_requirements():
    assert any("TELEGRAM_API_ID" in err for err in validate(good(), require_telegram=True))

def test_parse_env(tmp_path):
    p = tmp_path / ".env"; p.write_text("# comment\nPOSTGRES_PASSWORD=abc\n")
    assert parse_env(p)["POSTGRES_PASSWORD"] == "abc"
