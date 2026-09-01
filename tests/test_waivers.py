"""Tests for waiver wire analysis."""

from ff_tools.draft.rankings import RankedPlayer
from ff_tools.models.player import Player
from ff_tools.waivers.analyzer import WaiverAnalyzer, _index_rankings, _lookup


def _player(name: str, pos: str, pid: str = "", team: str = "ARI") -> Player:
    return Player(
        player_id=pid or f"sleeper-{name.lower().replace(' ', '-')}",
        full_name=name,
        position=pos,
        team=team,
    )


def _ranked(rank: int, name: str, pos: str, team: str = "ARI") -> RankedPlayer:
    """Rankings carry provider IDs disjoint from the roster feed's, on purpose."""
    return RankedPlayer(
        rank=rank,
        player=Player(
            player_id=f"fp-{rank}", full_name=name, position=pos, team=team
        ),
        tier=(rank - 1) // 12 + 1,
    )


# ── Cross-source matching ───────────────────────────────────────────


def test_lookup_matches_by_name_across_id_spaces():
    """The whole point: `fp-1` and `sleeper-...` never collide, names do."""
    rankings = [_ranked(1, "Ja'Marr Chase", "WR", "CIN")]
    by_id, by_key = _index_rankings(rankings)
    found = _lookup(_player("JaMarr Chase", "WR", team="CIN"), by_id, by_key)
    assert found is not None
    assert found.rank == 1


def test_lookup_prefers_id_when_it_hits():
    rankings = [_ranked(5, "Someone Else", "RB")]
    by_id, by_key = _index_rankings(rankings)
    found = _lookup(_player("Someone Else", "RB", pid="fp-5"), by_id, by_key)
    assert found is not None and found.rank == 5


def test_lookup_misses_return_none():
    by_id, by_key = _index_rankings([_ranked(1, "Known Guy", "WR")])
    assert _lookup(_player("Unknown Guy", "WR"), by_id, by_key) is None


def test_index_rankings_keeps_best_rank():
    """A duplicated player resolves to the better rank, whatever the order."""
    rankings = [_ranked(50, "Dup Player", "RB"), _ranked(10, "Dup Player", "RB")]
    _, by_key = _index_rankings(rankings)
    assert by_key["dup player"].rank == 10


# ── rank_free_agents ────────────────────────────────────────────────


def test_rank_free_agents_sorts_and_backfills_unranked():
    rankings = [_ranked(3, "Third", "WR"), _ranked(1, "First", "RB")]
    available = [
        _player("Nobody Knows Him", "TE"),
        _player("Third", "WR"),
        _player("First", "RB"),
    ]
    ranked = WaiverAnalyzer().rank_free_agents(available, rankings)

    assert [rp.display_name for rp in ranked] == ["First", "Third", "Nobody Knows Him"]
    assert [rp.rank for rp in ranked] == [1, 3, 4]


def test_rank_free_agents_unranked_sentinel_beats_a_sparse_board():
    """A short list with high ranks must still sort unknowns to the back."""
    rankings = [_ranked(120, "Deep Guy", "WR"), _ranked(300, "Deeper Guy", "WR")]
    ranked = WaiverAnalyzer().rank_free_agents(
        [_player("Nobody", "WR"), _player("Deeper Guy", "WR")], rankings
    )
    assert [rp.display_name for rp in ranked] == ["Deeper Guy", "Nobody"]
    assert ranked[-1].rank == 301


def test_rank_free_agents_keeps_the_platform_player_id():
    """The consensus rank is borrowed; the addable player object is not replaced."""
    rankings = [_ranked(7, "Rome Odunze", "WR", "CHI")]
    available = [_player("Rome Odunze", "WR", pid="sleeper-9999", team="CHI")]
    ranked = WaiverAnalyzer().rank_free_agents(available, rankings)

    assert ranked[0].rank == 7
    assert ranked[0].player.player_id == "sleeper-9999"


# ── find_drop_candidates ────────────────────────────────────────────


def test_find_drop_candidates_recommends_a_clear_upgrade():
    rankings = [
        _ranked(5, "Good Wr", "WR"),
        _ranked(20, "Star Qb", "QB"),
        _ranked(30, "Ok Wr", "WR"),
        _ranked(150, "Bad Wr", "WR"),
        _ranked(160, "Only Qb", "QB"),
    ]
    roster = [
        _player("Bad Wr", "WR"),
        _player("Good Wr", "WR"),
        _player("Ok Wr", "WR"),
        _player("Only Qb", "QB"),
    ]
    available = [_ranked(5, "Good Fa Wr", "WR"), _ranked(20, "Great Fa Qb", "QB")]

    recs = WaiverAnalyzer().find_drop_candidates(roster, available, rankings=rankings)

    # The lone QB is protected by MIN_ROSTERED even though a better one is free.
    assert [r.drop_player.full_name for r in recs] == ["Bad Wr"]
    assert recs[0].pickup_player.display_name == "Good Fa Wr"
    assert recs[0].upgrade_rank == 145


def test_find_drop_candidates_respects_position_minimums():
    """Two WRs rostered and a two-WR minimum means neither can go."""
    rankings = [_ranked(140, "Wr One", "WR"), _ranked(150, "Wr Two", "WR")]
    roster = [_player("Wr One", "WR"), _player("Wr Two", "WR")]
    available = [_ranked(1, "Elite Fa", "WR")]

    recs = WaiverAnalyzer().find_drop_candidates(roster, available, rankings=rankings)
    assert recs == []


def test_find_drop_candidates_never_offers_one_fa_twice():
    rankings = [
        _ranked(140, f"Wr {n}", "WR") for n in ("One", "Two", "Three", "Four")
    ]
    roster = [_player(f"Wr {n}", "WR") for n in ("One", "Two", "Three", "Four")]
    available = [_ranked(1, "Fa A", "WR"), _ranked(2, "Fa B", "WR")]

    recs = WaiverAnalyzer().find_drop_candidates(roster, available, rankings=rankings)

    pickups = [r.pickup_player.display_name for r in recs]
    assert sorted(pickups) == ["Fa A", "Fa B"]
    assert len(set(r.drop_player.full_name for r in recs)) == len(recs)


def test_find_drop_candidates_unranked_roster_player_is_most_droppable():
    # A realistically deep board, so "unranked" really does mean far off the end.
    rankings = [_ranked(1, "Wr Keeper", "WR"), _ranked(2, "Wr Keeper Two", "WR")]
    rankings += [_ranked(n, f"Filler {n}", "RB") for n in range(3, 60)]
    roster = [
        _player("Wr Keeper", "WR"),
        _player("Wr Keeper Two", "WR"),
        _player("Never Heard Of Him", "WR"),
    ]
    available = [_ranked(1, "Fa Starter", "WR")]

    recs = WaiverAnalyzer().find_drop_candidates(roster, available, rankings=rankings)
    assert len(recs) == 1
    assert recs[0].drop_player.full_name == "Never Heard Of Him"
    assert "unranked" in recs[0].reasoning


def test_find_drop_candidates_ignores_marginal_upgrades():
    """An upgrade inside the threshold is churn, not an improvement."""
    rankings = [_ranked(60, "Wr A", "WR"), _ranked(61, "Wr B", "WR"), _ranked(62, "Wr C", "WR")]
    roster = [_player("Wr A", "WR"), _player("Wr B", "WR"), _player("Wr C", "WR")]
    available = [_ranked(55, "Fa Marginal", "WR")]

    analyzer = WaiverAnalyzer()
    assert analyzer.find_drop_candidates(roster, available, rankings=rankings) == []
    # Lower the bar and the same pairing qualifies.
    loosened = analyzer.find_drop_candidates(
        roster, available, rankings=rankings, upgrade_threshold=2
    )
    assert len(loosened) == 1


def test_find_drop_candidates_legacy_two_arg_call_is_degraded_not_broken():
    """Without `rankings` the roster cannot be ranked, so nothing is proposed."""
    roster = [_player("Bad Wr", "WR"), _player("Ok Wr", "WR"), _player("Third Wr", "WR")]
    available = [_ranked(1, "Elite Fa", "WR")]
    assert WaiverAnalyzer().find_drop_candidates(roster, available) == []


def test_find_drop_candidates_custom_minimums():
    rankings = [_ranked(150, "Only Te", "TE")]
    roster = [_player("Only Te", "TE")]
    available = [_ranked(1, "Elite Te", "TE")]

    analyzer = WaiverAnalyzer()
    assert analyzer.find_drop_candidates(roster, available, rankings=rankings) == []
    allowed = analyzer.find_drop_candidates(
        roster, available, rankings=rankings, min_rostered={"TE": 0}
    )
    assert len(allowed) == 1


# ── FAAB ────────────────────────────────────────────────────────────


def test_recommend_faab_scales_with_tier():
    analyzer = WaiverAnalyzer()
    elite = analyzer.recommend_faab(_ranked(1, "Elite", "RB"), remaining_budget=100)
    deep = analyzer.recommend_faab(_ranked(200, "Deep", "RB"), remaining_budget=100)
    assert elite.recommended_bid > deep.recommended_bid
    assert elite.max_bid <= 100


def test_recommend_faab_low_budget_is_conservative():
    analyzer = WaiverAnalyzer()
    flush = analyzer.recommend_faab(_ranked(1, "Elite", "RB"), remaining_budget=100)
    broke = analyzer.recommend_faab(_ranked(1, "Elite", "RB"), remaining_budget=10)
    assert broke.recommended_bid < flush.recommended_bid
    assert "limited budget" in broke.reasoning


def test_recommend_faab_positional_need_raises_the_bid():
    analyzer = WaiverAnalyzer()
    base = analyzer.recommend_faab(_ranked(30, "Mid", "TE"), remaining_budget=50)
    starved = analyzer.recommend_faab(
        _ranked(30, "Mid", "TE"), remaining_budget=50, is_starved_at_position=True
    )
    assert starved.recommended_bid > base.recommended_bid


def test_recommend_faab_bid_stays_in_range():
    analyzer = WaiverAnalyzer()
    for rank in (1, 13, 25, 60, 300):
        rec = analyzer.recommend_faab(
            _ranked(rank, "P", "RB"), remaining_budget=100, is_starved_at_position=True
        )
        assert 1 <= rec.recommended_bid <= 80
        assert rec.recommended_bid <= rec.max_bid <= 100


# ── build_target_list ───────────────────────────────────────────────


def test_build_target_list_boosts_trending_and_needs():
    rankings = [_ranked(40, "Quiet Guy", "WR"), _ranked(45, "Hot Guy", "RB")]
    available = [_player("Quiet Guy", "WR"), _player("Hot Guy", "RB")]
    targets = WaiverAnalyzer().build_target_list(
        available,
        rankings,
        remaining_budget=100,
        positions_needed=["RB"],
        trending_adds={_player("Hot Guy", "RB").player_id: 5000},
    )
    # Worse consensus rank, but trending hard and fills a need.
    assert targets[0].player.display_name == "Hot Guy"
    assert targets[0].trending_adds == 5000
    assert "Fills RB need" in targets[0].reasoning
