from atom_core.red_info import _con_sufijo_local, info_red


def test_con_sufijo_local_anade_local_a_hostname_pelado():
    assert _con_sufijo_local("organizer") == "organizer.local"


def test_con_sufijo_local_no_duplica_si_ya_lo_trae():
    assert _con_sufijo_local("organizer.local") == "organizer.local"


def test_con_sufijo_local_no_lo_anade_a_una_ip():
    assert _con_sufijo_local("192.168.1.119") == "192.168.1.119"


def test_con_sufijo_local_vacio_se_queda_vacio():
    assert _con_sufijo_local("") == ""


def test_info_red_url_usa_hostname_con_local(monkeypatch):
    import atom_core.red_info as red_info

    monkeypatch.setattr(red_info, "hostname", lambda: "organizer")
    monkeypatch.setattr(red_info, "ips_locales", lambda: [])
    d = info_red()
    assert d["hostname"] == "organizer"
    assert d["url"] == "http://organizer.local"


def test_info_red_sin_hostname_cae_a_ip_sin_local(monkeypatch):
    import atom_core.red_info as red_info

    monkeypatch.setattr(red_info, "hostname", lambda: "")
    monkeypatch.setattr(red_info, "ips_locales", lambda: [{"interfaz": "wlan0", "ip": "10.42.0.1"}])
    d = info_red()
    assert d["url"] == "http://10.42.0.1"
