# CUI // SP-CTI
"""Network Design Canvas — Project management routes."""


def register_projects_routes(bp, get_conn=None, helpers=None):
    """Register project CRUD routes on the NDC blueprint.

    Parameters
    ----------
    bp : Flask Blueprint
    get_conn : callable returning DB connection
    helpers : module with _now, _row_to_dict, _audit, _crud_list, etc.
    """

    @bp.route("/api/projects", methods=["GET"])
    def ndc_list_projects():
        from flask import jsonify

        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM ndc_designs ORDER BY updated_at DESC").fetchall()
            return jsonify({"designs": [dict(r) for r in rows]})
        finally:
            conn.close()
