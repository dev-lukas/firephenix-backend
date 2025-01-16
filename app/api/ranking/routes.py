from flask import Blueprint, jsonify, request

ranking_bp = Blueprint('ranking', __name__)

@ranking_bp.route('/api/ranking', methods=['GET'])
def get_ranking():
    rankings = [
        {'position': 1, 'name': 'Item 1', 'score': 100},
        {'position': 2, 'name': 'Item 2', 'score': 90},
        {'position': 3, 'name': 'Item 3', 'score': 80}
    ]
    return jsonify(rankings)