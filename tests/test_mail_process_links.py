from backend.routers import informs


def test_inform_mail_uses_inform_specific_process_link():
    assert informs._GO_FLOW_URL == "http://go/flow_process-inform"

