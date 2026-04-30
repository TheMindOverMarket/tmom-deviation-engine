import time
import unittest

from deviation.engine import DeviationEngine, DeviationEngineRegistry
from deviation.market_adapter import MarketAdapter, MarketQuote
from deviation.models import DeviationType, ExpiryPolicy


class StaticMarketAdapter(MarketAdapter):
    def __init__(self, price: float = 100.0):
        self.price = price

    def get_best_bid(self, symbol, ts_ms=None):
        return self.price

    def get_best_ask(self, symbol, ts_ms=None):
        return self.price

    def get_mid_price(self, symbol, ts_ms=None):
        return self.price

    def get_last_trade(self, symbol, ts_ms=None):
        return self.price

    def get_quote(self, symbol, ts_ms=None):
        return MarketQuote(symbol=symbol, bid=self.price, ask=self.price, mid=self.price, last_trade=self.price)


def make_engine(session_id: str = "session-1", expected_qty: float = 0.001) -> DeviationEngine:
    return DeviationEngine(
        session_id=session_id,
        playbook_id="playbook-1",
        user_id="user-1",
        market_adapter=StaticMarketAdapter(),
        default_expected_qty=expected_qty,
    )


class DeviationEngineRegressionTests(unittest.TestCase):
    def test_compliant_entry_has_no_deviation_cost(self):
        engine = make_engine("compliant")
        engine.register_compliant_action("BTC/USD", "buy", ["rule"], canonical_price=100.0)

        result = engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "order-1")

        self.assertEqual(result["deviations"], [])
        self.assertEqual(result["session_totals"]["total_deviation_cost"], 0.0)
        self.assertEqual(result["session_totals"]["pending_finalization"], 0)

    def test_oversize_cost_is_immediate(self):
        engine = make_engine("oversize")
        engine.register_compliant_action("BTC/USD", "buy", ["rule"], canonical_price=100.0)

        result = engine.process_decision("BTC/USD", "buy", 0.0012, 0.0012, 110.0, "order-1")

        self.assertEqual(result["deviations"][0]["deviation_type"], DeviationType.OVERSIZE.value)
        self.assertAlmostEqual(result["deviations"][0]["candidate_cost"], 0.002)
        self.assertAlmostEqual(result["session_totals"]["total_deviation_cost"], 0.002)

    def test_undersize_has_no_cost(self):
        engine = make_engine("undersize")
        engine.register_compliant_action("BTC/USD", "buy", ["rule"], canonical_price=100.0)

        result = engine.process_decision("BTC/USD", "buy", 0.0008, 0.0008, 100.0, "order-1")

        self.assertEqual(result["deviations"][0]["deviation_type"], DeviationType.UNDERSIZE.value)
        self.assertIsNone(result["deviations"][0]["candidate_cost"])
        self.assertEqual(result["session_totals"]["total_deviation_cost"], 0.0)

    def test_invalid_entry_losing_close_finalizes_cost_and_clears_pending(self):
        engine = make_engine("invalid-loss")

        buy = engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "buy-1")
        sell = engine.process_decision("BTC/USD", "sell", 0.001, 0.001, 90.0, "sell-1")

        self.assertEqual(buy["deviations"][0]["deviation_type"], DeviationType.INVALID_TRADE.value)
        self.assertEqual(sell["deviations"], [])
        self.assertAlmostEqual(sell["finalized"][0]["finalized_cost"], 0.01)
        self.assertAlmostEqual(sell["session_totals"]["total_deviation_cost"], 0.01)
        self.assertEqual(sell["session_totals"]["pending_finalization"], 0)

    def test_invalid_entry_partial_closes_accumulate_until_flat(self):
        engine = make_engine("invalid-partial-close")

        engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "buy-1")
        first_close = engine.process_decision("BTC/USD", "sell", 0.0004, 0.0004, 90.0, "sell-1")
        second_close = engine.process_decision("BTC/USD", "sell", 0.0006, 0.0006, 80.0, "sell-2")

        self.assertAlmostEqual(first_close["finalized"][0]["finalized_cost"], 0.004)
        self.assertEqual(first_close["session_totals"]["pending_finalization"], 1)
        self.assertAlmostEqual(second_close["finalized"][0]["finalized_cost"], 0.016)
        self.assertAlmostEqual(second_close["session_totals"]["total_deviation_cost"], 0.016)
        self.assertEqual(second_close["session_totals"]["pending_finalization"], 0)

    def test_invalid_entry_winning_close_records_unauthorized_gain(self):
        engine = make_engine("invalid-gain")

        engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "buy-1")
        sell = engine.process_decision("BTC/USD", "sell", 0.001, 0.001, 110.0, "sell-1")

        self.assertAlmostEqual(sell["finalized"][0]["finalized_cost"], 0.0)
        self.assertAlmostEqual(sell["finalized"][0]["unauthorized_gain"], 0.01)
        self.assertAlmostEqual(sell["session_totals"]["total_unauthorized_gain"], 0.01)
        self.assertEqual(sell["session_totals"]["pending_finalization"], 0)

    def test_duplicate_order_event_is_noop(self):
        engine = make_engine("duplicate")

        first = engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "order-1")
        duplicate = engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 100.0, "order-1")

        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["session_totals"]["trade_count"], 1)
        self.assertEqual(duplicate["session_totals"]["deviation_count"], 1)

    def test_late_entry_cost_is_immediate(self):
        engine = make_engine("late-entry")
        action = engine.register_compliant_action(
            "BTC/USD",
            "buy",
            ["rule"],
            canonical_price=100.0,
            expiry_policy=ExpiryPolicy.WINDOW_N_SECONDS,
            expiry_seconds=1.0,
        )
        late_ts = (action.expiry_at or time.time() * 1000) + 5_000

        result = engine.process_decision("BTC/USD", "buy", 0.001, 0.001, 110.0, "order-1", timestamp_ms=late_ts)

        self.assertEqual(result["deviations"][0]["deviation_type"], DeviationType.LATE_ENTRY.value)
        self.assertAlmostEqual(result["deviations"][0]["candidate_cost"], 0.01)
        self.assertAlmostEqual(result["session_totals"]["total_deviation_cost"], 0.01)

    def test_risk_context_can_emit_process_deviation(self):
        engine = make_engine("risk-context")
        engine.register_compliant_action("BTC/USD", "buy", ["rule"], canonical_price=100.0)

        result = engine.process_decision(
            "BTC/USD",
            "buy",
            0.001,
            0.001,
            100.0,
            "order-1",
            position_context={"cooldown_violated": True},
        )

        self.assertEqual(result["deviations"][0]["deviation_type"], DeviationType.COOLDOWN_VIOLATION.value)

    def test_registry_reuses_existing_engine_for_same_session(self):
        session_id = "registry-reuse"
        DeviationEngineRegistry.remove(session_id)
        first = DeviationEngineRegistry.create(session_id, "playbook-1", "user-1", StaticMarketAdapter())
        second = DeviationEngineRegistry.create(session_id, "playbook-2", "user-2", StaticMarketAdapter())
        DeviationEngineRegistry.remove(session_id)

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
