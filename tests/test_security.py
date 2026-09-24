from hip_id_agent.security import mask_sensitive_data, mask_sensitive_string


def test_mask_sensitive_dict():
    data = {"authorization": "Bearer abc.def.ghi", "safe": "hello", "nested": {"sftp_password": "secret"}}
    masked = mask_sensitive_data(data)
    assert masked["authorization"] == "***MASKED***"
    assert masked["nested"]["sftp_password"] == "***MASKED***"
    assert masked["safe"] == "hello"


def test_mask_bearer_text():
    assert "***MASKED***" in mask_sensitive_string("Authorization: Bearer abcdef123")
