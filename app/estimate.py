
def _json_safe(obj):
    """Recursively make a structure JSON-safe (no NaN/Inf/datetime surprises)."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    # numpy scalars etc.
    try:
        if hasattr(obj, "item"):
            return _json_safe(obj.item())
    except Exception:
        pass
    try:
        return float(obj)
    except Exception:
        return str(obj)


from flask import Blueprint, jsonify, request
import math
from datetime import date, datetime
from flask_login import current_user, login_required

from app.assumptions import (
    MONETARY_DISCLAIMER,
    SUBSIDY_CAVEAT,
    SUBSIDY_ELIGIBILITY_ASSUMPTION,
)
from app.services import estimation_service

estimate_bp = Blueprint("estimate", __name__)


@estimate_bp.route("/api/estimate", methods=["POST"])
@login_required
def api_estimate():
    payload = request.get_json(silent=True) or {}

    try:
        estimate_inputs = estimation_service.prepare_estimate_inputs(payload)
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result, location = estimation_service.create_estimate(
            user_id=current_user.id,
            **estimate_inputs,
        )
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Estimation engine failed: {exc}"}), 500
    except estimation_service.EstimatePersistenceError as exc:
        if exc.schema_outdated:
            return (
                jsonify(
                    {
                        "error": (
                            "Database schema is outdated (new orientation/battery fields). "
                            "Stop the server, delete the file solar_app.db next to run.py, "
                            "then start again with: python run.py"
                        )
                    }
                ),
                500,
            )
        return jsonify({"error": f"Could not save estimate: {exc}"}), 500

    response = result.copy()
    response["location_id"] = location.id
    response["redirect_url"] = f"/report/{location.id}"
    response["disclaimer"] = MONETARY_DISCLAIMER
    response["subsidy_caveat"] = SUBSIDY_CAVEAT
    response["subsidy_eligibility_assumption"] = SUBSIDY_ELIGIBILITY_ASSUMPTION
    try:
        return jsonify(_json_safe(response))
    except Exception as exc:
        # Last-resort: never return a non-JSON 200 to the browser
        return jsonify({"error": f"Could not serialize estimate response: {exc}"}), 500


@estimate_bp.route("/api/estimate/<int:location_id>/orientation-preview", methods=["POST"])
@login_required
def api_orientation_preview(location_id):
    """Preview generation/financial impact while the report compass is dragged."""
    location = estimation_service.UserLocation.query.filter_by(
        id=location_id, user_id=current_user.id
    ).first()
    if location is None:
        return jsonify({"error": "Estimate not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = estimation_service.preview_orientation(
            location, payload.get("orientation")
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Orientation preview failed: {exc}"}), 500

    return jsonify(_json_safe({
        "orientation_deg": result.get("orientation_deg"),
        "orientation_label": result.get("orientation_label"),
        "orientation_factor": result.get("orientation_factor"),
        "annual_generation": result.get("annual_generation"),
        "monthly_savings": result.get("monthly_savings"),
        "payback_years": result.get("payback_years"),
        "monthly_data": result.get("monthly_data"),
    }))


@estimate_bp.route("/api/estimate/<int:location_id>/orientation", methods=["POST"])
@login_required
def api_apply_orientation(location_id):
    """Persist the orientation selected with the report compass."""
    location = estimation_service.UserLocation.query.filter_by(
        id=location_id, user_id=current_user.id
    ).first()
    if location is None:
        return jsonify({"error": "Estimate not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = estimation_service.apply_orientation(
            location, payload.get("orientation")
        )
    except estimation_service.InvalidEstimateInputError as exc:
        return jsonify({"error": str(exc)}), 400
    except estimation_service.EstimationEngineError as exc:
        return jsonify({"error": f"Could not apply orientation: {exc}"}), 500
    except Exception as exc:
        estimation_service.db.session.rollback()
        return jsonify({"error": f"Could not save orientation: {exc}"}), 500

    return jsonify(_json_safe({
        "orientation_deg": result.get("orientation_deg"),
        "orientation_label": result.get("orientation_label"),
        "orientation_factor": result.get("orientation_factor"),
        "annual_generation": result.get("annual_generation"),
        "monthly_savings": result.get("monthly_savings"),
        "payback_years": result.get("payback_years"),
    }))


@estimate_bp.route("/api/tariff", methods=["GET"])
@login_required
def api_tariff_defaults():
    """Return seeded residential tariff + net-metering defaults for a state."""
    state = (request.args.get("state") or "").strip()
    info = estimation_service.lookup_tariff_defaults(state or None)
    return jsonify(info)


@estimate_bp.route("/api/location-context", methods=["GET"])
@login_required
def api_location_context():
    """Best-effort DISCOM + scheme + tariff defaults for a place."""
    from app.discom_lookup import lookup_location_context

    def _f(name):
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    ctx = lookup_location_context(
        state=(request.args.get("state") or "").strip() or None,
        city=(request.args.get("city") or "").strip() or None,
        address=(request.args.get("address") or "").strip() or None,
        pincode=(request.args.get("pincode") or "").strip() or None,
        latitude=_f("lat"),
        longitude=_f("lng"),
    )
    return jsonify(ctx)
