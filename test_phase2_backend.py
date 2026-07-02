"""
Phase 2 verification tests: models, helpers, Flask routes.
Run: python -m pytest test_phase2_backend.py -v
"""

import os
import sys
import json
import pytest

os.environ['FLASK_ENV'] = 'testing'


@pytest.fixture(scope="module")
def app():
    from app import app as flask_app
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    flask_app.config['TESTING'] = True
    from models import db
    with flask_app.app_context():
        db.create_all()
    return flask_app


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


@pytest.fixture
def db_session(app):
    from models import db
    with app.app_context():
        yield db


# ── Test 1: Module imports ──

def test_imports():
    import shapely
    import sqlalchemy
    from bs4 import BeautifulSoup
    assert shapely.__version__
    assert sqlalchemy.__version__
    assert BeautifulSoup


# ── Test 2: Database tables exist ──

def test_db_tables_exist(app):
    from models import db
    with app.app_context():
        conn = db.engine.connect()
        assert db.engine.dialect.has_table(conn, 'parcels')
        assert db.engine.dialect.has_table(conn, 'parcel_vertices')
        assert db.engine.dialect.has_table(conn, 'ldm_reports')
        conn.close()


# ── Test 3: Parcel insert, query, cascade delete ──

def test_parcel_crud(app):
    from models import db, Parcel, ParcelVertex, LdmReport

    with app.app_context():
        parcel = Parcel(
            plot_id="456",
            plot_no="789",
            khata_no="KH-001",
            pniu="12345678901234",
            area=6070.0,
            perimeter=320.5,
            lat=25.5,
            lon=85.1,
            owner_names='["Ram Singh", "Shyam Singh"]',
            district="Patna",
            circle="Patna Sadar",
            mouza="Test Mouza",
            sheet_no="S-01"
        )
        db.session.add(parcel)
        db.session.flush()

        for i in range(5):
            v = ParcelVertex(
                parcel_id=parcel.id,
                x=100.0 + i * 10, y=200.0 + i * 10,
                lon=85.0 + i * 0.001, lat=25.0 + i * 0.001,
                sequence_order=i
            )
            db.session.add(v)

        report = LdmReport(
            parcel_id=parcel.id,
            report_url="/10/pdf/test.pdf",
            filename="reports/10_test_789.pdf"
        )
        db.session.add(report)
        db.session.commit()

        pid = parcel.id

        fetched = db.session.get(Parcel, pid)
        assert fetched.khata_no == "KH-001"
        assert fetched.pniu == "12345678901234"
        assert len(fetched.vertices) == 5
        assert fetched.report is not None
        assert fetched.report.filename == "reports/10_test_789.pdf"

        assert fetched.vertices[0].sequence_order == 0
        assert fetched.vertices[4].sequence_order == 4

        db.session.delete(fetched)
        db.session.commit()

        assert db.session.get(Parcel, pid) is None
        assert db.session.query(ParcelVertex).filter_by(parcel_id=pid).count() == 0
        assert db.session.query(LdmReport).filter_by(parcel_id=pid).count() == 0


# ── Test 7: Home route ──

def test_home_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Bihar Cadastral" in resp.data or b"Bhu-Overlay" in resp.data


# ── Test 8: Export routes return 404 for missing parcels ──

def test_export_missing(client):
    assert client.get("/proxy/Export/GeoJSON/nonexist").status_code == 404
    assert client.get("/proxy/Export/CSV/nonexist").status_code == 404
    assert client.get("/static/reports/nonexist.pdf").status_code == 404


# ── Test 9: Plot details route validates inputs ──

def test_plot_details_validation(client):
    resp = client.post("/proxy/MapInfo/getPlotDetailsAndInspection", data={"state": "10"})
    assert resp.status_code == 400

    resp = client.post("/proxy/MapInfo/getPlotDetailsAndInspection",
                       data={"state": "10", "giscode": "abc"})
    assert resp.status_code == 400


# ── Test 10: WKT parsing ──

def test_wkt_parsing():
    from shapely import wkt

    geom = wkt.loads("POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))")
    assert geom.geom_type == 'Polygon'
    assert abs(geom.area - 100.0) < 0.01
    assert abs(geom.length - 40.0) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
