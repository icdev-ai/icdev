"""Step definitions for health check feature."""

from pytest_bdd import given, when, then, parsers, scenarios

scenarios("../features/health.feature")


@given("the application is running")
def app_running(client):
    """Ensure app is available via test client fixture."""
    pass


@when("I request the health endpoint", target_fixture="response")
def request_health(client):
    return client.get("/health")


@then("I should receive a 200 status code")
def check_status(response):
    assert response.status_code == 200


@then(parsers.parse('the response should contain "{text}"'))
def check_content(response, text):
    assert text in response.get_data(as_text=True)
