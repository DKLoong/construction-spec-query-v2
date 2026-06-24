from app.auth import hash_password, verify_password, create_access_token, decode_access_token

def test_hash_and_verify_password():
    password = "test-password-123"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong", hashed) is False

def test_create_and_decode_token():
    data = {"sub": "admin"}
    token = create_access_token(data)
    assert isinstance(token, str)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "admin"

def test_decode_expired_token():
    from datetime import datetime, timedelta, timezone
    from jose import jwt
    from app.config import SECRET_KEY, ALGORITHM
    expire = datetime.now(timezone.utc) - timedelta(minutes=1)
    token = jwt.encode({"sub": "admin", "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)
    import pytest
    with pytest.raises(Exception):
        decode_access_token(token)
