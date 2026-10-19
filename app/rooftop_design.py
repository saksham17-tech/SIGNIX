from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from app.models import db
from app.services import rooftop_design_service
from app.services import rooftop_ingestion_service
from app.services import rooftop_layout_service


rooftop_design_bp = Blueprint("rooftop_design", __name__)


@rooftop_design_bp.post("/api/locations/<int:location_id>/rooftop-layout/accept")
@login_required
def accept_rooftop_layout(location_id):
    try:
        result = rooftop_layout_service.accept_layout(
            location_id, current_user.id, request.get_json(silent=True)
        )
    except rooftop_layout_service.LayoutAcceptanceError as exc:
        db.session.rollback()
        return jsonify(error=str(exc), code=exc.code, details=exc.details), exc.status
    except rooftop_design_service.RooftopDesignInputError as exc:
        db.session.rollback()
        return jsonify(error=str(exc), code="invalid_design"), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Rooftop layout acceptance failed")
        return jsonify(error="Layout recalculation could not be completed", code="recalculation_failed"), 500
    if result is None:
        return jsonify(error="Location not found", code="location_not_found"), 404
    return jsonify(result), 200 if result.get("idempotent") else 201


@rooftop_design_bp.get("/api/locations/<int:location_id>/rooftop-design")
@login_required
def current_rooftop_design(location_id):
    design, data = rooftop_design_service.get_current_design(
        location_id, current_user.id
    )
    if design is None:
        if rooftop_design_service.get_owned_location(location_id, current_user.id) is None:
            return jsonify(error="Location not found"), 404
        return jsonify(error="Rooftop design not found"), 404
    return jsonify(data)


@rooftop_design_bp.post("/api/locations/<int:location_id>/rooftop-design")
@login_required
def create_rooftop_design_revision(location_id):
    try:
        design = rooftop_design_service.create_revision(
            location_id, current_user.id, request.get_json(silent=True) or {}
        )
    except rooftop_design_service.RooftopDesignInputError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    if design is None:
        return jsonify(error="Location not found"), 404
    return jsonify(design.to_dict()), 201


@rooftop_design_bp.post("/api/locations/<int:location_id>/rooftop-design/ingest")
@login_required
def ingest_rooftop_design_data(location_id):
    payload = request.get_json(silent=True) or {}
    providers = payload.get("providers") or ["google_solar"]
    if not isinstance(providers, list) or not set(providers).issubset(
        {"google_solar", "gemini"}
    ):
        return jsonify(error="providers must contain google_solar and/or gemini"), 400
    try:
        result = rooftop_ingestion_service.ingest(
            location_id,
            current_user.id,
            providers=providers,
            include_data_layers=bool(payload.get("include_data_layers", False)),
            refresh=bool(payload.get("refresh", False)),
        )
    except rooftop_design_service.RooftopDesignInputError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Rooftop data ingestion failed")
        return jsonify(error="Rooftop data ingestion could not be completed"), 502
    if result is None:
        return jsonify(error="Location not found"), 404
    return jsonify(result)


@rooftop_design_bp.patch(
    "/api/locations/<int:location_id>/rooftop-design/<int:design_id>"
    "/sources/<int:source_id>"
)
@login_required
def review_rooftop_design_source(location_id, design_id, source_id):
    try:
        design = rooftop_design_service.review_source(
            location_id,
            design_id,
            source_id,
            current_user.id,
            request.get_json(silent=True) or {},
        )
    except rooftop_design_service.RooftopDesignInputError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    if design is None:
        return jsonify(error="Rooftop design source not found"), 404
    return jsonify(design.to_dict()), 201
