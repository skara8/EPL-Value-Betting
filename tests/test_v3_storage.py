import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); APP=os.path.join(ROOT,"app")
if APP not in sys.path: sys.path.insert(0,APP)

import edge_model
import engine
import v3_storage
from execution_v3 import V3MatchPriceShop, V3PriceQuote
from independent_model_v24 import HistoricalMatch, LEAGUE_SOURCES
from model_v3 import apply_v3_forecasts, build_v3_forecasts
from strategy_v3 import decision_for_side
from validation_v3 import walk_forward_validate


class V3StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=v3_storage.DB_FILE
        v3_storage.DB_FILE=Path(self.tmp.name)/"v3-test.db"

    def tearDown(self):
        v3_storage.DB_FILE=self.old_db
        self.tmp.cleanup()

    @staticmethod
    def source():
        return next(s for s in LEAGUE_SOURCES if s.key=="ENG-PL")

    @staticmethod
    def history(count=180):
        start=datetime(2024,7,1,tzinfo=timezone.utc); teams=("alpha","beta","gamma","delta","epsilon","zeta"); output=[]
        for i in range(count):
            home=teams[i%6]; away=teams[(i*3+1)%6]
            if home==away: away=teams[(i+2)%6]
            hg=1+int(teams.index(home)<teams.index(away))+int(i%5==0)
            ag=int(teams.index(away)<=teams.index(home))+int(i%8==0)
            output.append(HistoricalMatch(start+timedelta(days=i*3),"2425" if i<count//2 else "2526","ENG-PL",home,away,hg,ag))
        return output

    def _row(self):
        row=engine.CombinedMatch(kickoff=datetime(2026,8,29,20,0,tzinfo=engine.BRISBANE),home_team="Alpha",away_team="Beta",sb_home=2.10,sb_draw=3.50,sb_away=3.80,pm_fair_home=.48,pm_fair_draw=.27,pm_fair_away=.25)
        row.league="England Premier League"
        edge_model.calculate_match_edge(row,4.0)
        forecast=build_v3_forecasts([row],{"ENG-PL":self.history()},bootstrap_samples=4)[row.match_name]
        apply_v3_forecasts([row],{row.match_name:forecast},4.0)
        shop=V3MatchPriceShop(row.match_name,row.league)
        shop.model_probability={"HOME":forecast.home_probability,"DRAW":forecast.draw_probability,"AWAY":forecast.away_probability}
        quote=V3PriceQuote("UnitBook","HOME",2.25,"2.25",datetime.now(timezone.utc).isoformat(),match_confidence=.99)
        shop.quotes["HOME"].append(quote); shop.best["HOME"]=quote; shop.best_ev_pct["HOME"]=(forecast.home_probability*2.25-1)*100
        row.price_shop=shop
        return row

    def test_forecast_quote_decision_and_validation_round_trip(self):
        row=self._row(); decision=decision_for_side(row,"HOME",4.0); self.assertIsNotNone(decision)
        counts=v3_storage.save_v3_snapshot([row],[decision]); self.assertEqual(counts,(1,1,1)); self.assertEqual(v3_storage.v3_counts()[:3],(1,1,1))
        report=walk_forward_validate(self.source(),self.history(),min_train_matches=110,fold_size=35,max_folds=1); self.assertGreater(report.predictions,0)
        run_id=v3_storage.save_validation_report(report); self.assertGreater(run_id,0); self.assertEqual(v3_storage.v3_counts()[3],1)
        with v3_storage._db() as con:
            stored=con.execute("SELECT model_version,feature_schema_version,feature_snapshot_hash FROM v3_forecasts").fetchone()
            self.assertEqual(stored[0],"3.0.0"); self.assertEqual(stored[1],"v3.0"); self.assertEqual(len(stored[2]),64)
            quote=con.execute("SELECT source,decimal_odds,match_confidence,is_best FROM v3_quotes").fetchone()
            self.assertEqual(quote[0],"UnitBook"); self.assertAlmostEqual(quote[1],2.25); self.assertAlmostEqual(quote[2],.99); self.assertEqual(quote[3],1)


if __name__=="__main__": unittest.main()
