from app.models import Job
from app.services.matcher import _location_matches

def test_clear_europe_remote():
    job = Job(title="Backend Engineer", location="Remote - London, UK", is_remote=True, description="")
    is_match, region = _location_matches(job)
    assert is_match is True
    assert region in ["UNITED KINGDOM", "UK", "LONDON"]

def test_clear_europe_synonym():
    job = Job(title="Backend Engineer", location="Remote (EMEA)", is_remote=True, description="")
    is_match, region = _location_matches(job)
    assert is_match is True
    assert region == "EMEA"

def test_clear_us_only_remote():
    job = Job(title="Backend Engineer", location="Remote (US)", is_remote=True, description="Must be based in the United States.")
    is_match, region = _location_matches(job)
    assert is_match is False

def test_onsite_berlin():
    job = Job(title="Backend Engineer", location="Berlin, Germany", is_remote=False, description="On-site in Berlin.")
    is_match, region = _location_matches(job)
    assert is_match is False

def test_remote_worldwide_flagged():
    job = Job(title="Backend Engineer", location="Worldwide", is_remote=True, description="Fully distributed team.")
    is_match, region = _location_matches(job)
    assert is_match is True
    assert region == "WORLDWIDE"

def test_ambiguous_location():
    job = Job(title="Backend Engineer", location="", is_remote=True, description="Remote work.")
    is_match, region = _location_matches(job)
    assert is_match is False  # Excluded by default

def test_remote_us_only_in_description():
    job = Job(title="Backend Engineer", location="Worldwide", is_remote=True, description="Only United States candidates will be considered.")
    is_match, region = _location_matches(job)
    assert is_match is False  # Should be excluded despite "Worldwide" location

def test_turkey_russia_included():
    job1 = Job(title="Backend", location="Istanbul, Turkey", is_remote=True, description="")
    is_match, region = _location_matches(job1)
    assert is_match is True
    assert region in ["TURKEY", "ISTANBUL"]

    job2 = Job(title="Backend", location="Moscow, Russia", is_remote=True, description="")
    is_match, region = _location_matches(job2)
    assert is_match is True
    assert region in ["RUSSIA", "MOSCOW"]
