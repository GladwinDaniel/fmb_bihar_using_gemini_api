from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Parcel(db.Model):
    __tablename__ = 'parcels'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    plot_id = db.Column(db.String(100), nullable=True)  # Alphanumeric ID
    plot_no = db.Column(db.String(50), nullable=False)  # Plot Number (e.g. 1587)
    khata_no = db.Column(db.String(50), nullable=True)
    pniu = db.Column(db.String(50), nullable=True)
    area = db.Column(db.Float, nullable=True)  # in square meters
    perimeter = db.Column(db.Float, nullable=True)  # in meters
    lat = db.Column(db.Float, nullable=True)  # Centroid Lat
    lon = db.Column(db.Float, nullable=True)  # Centroid Lon
    district = db.Column(db.String(100), nullable=True)
    subdivision = db.Column(db.String(100), nullable=True)
    circle = db.Column(db.String(100), nullable=True)
    mouza = db.Column(db.String(100), nullable=True)
    survey = db.Column(db.String(50), nullable=True)
    mapinst = db.Column(db.String(50), nullable=True)
    sheet_no = db.Column(db.String(50), nullable=True)
    owner_names = db.Column(db.Text, nullable=True)  # JSON-serialized list of owner names

    vertices = db.relationship('ParcelVertex', backref='parcel', cascade='all, delete-orphan', lazy=True)
    segments = db.relationship('BoundarySegment', backref='parcel', cascade='all, delete-orphan', lazy=True)
    report = db.relationship('LdmReport', backref='parcel', uselist=False, cascade='all, delete-orphan', lazy=True)

class ParcelVertex(db.Model):
    __tablename__ = 'parcel_vertices'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey('parcels.id'), nullable=False)
    x = db.Column(db.Float, nullable=False)  # Native UTM coordinate
    y = db.Column(db.Float, nullable=False)  # Native UTM coordinate
    lon = db.Column(db.Float, nullable=False)  # Transformed WGS84 GPS coordinate
    lat = db.Column(db.Float, nullable=False)  # Transformed WGS84 GPS coordinate
    sequence_order = db.Column(db.Integer, nullable=False)

class BoundarySegment(db.Model):
    __tablename__ = 'boundary_segments'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey('parcels.id'), nullable=False)
    start_vertex_index = db.Column(db.Integer, nullable=False)
    end_vertex_index = db.Column(db.Integer, nullable=False)
    length_meters = db.Column(db.Float, nullable=False)
    bearing = db.Column(db.Float, nullable=True)  # 0 to 360 degrees bearing angle

class LdmReport(db.Model):
    __tablename__ = 'ldm_reports'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    parcel_id = db.Column(db.Integer, db.ForeignKey('parcels.id'), nullable=False)
    report_url = db.Column(db.String(255), nullable=False)  # Local static report file URL
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
