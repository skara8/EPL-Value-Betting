import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),"..")); APP=os.path.join(ROOT,"app")
if APP not in sys.path: sys.path.insert(0,APP)

import edge_model
import engine
from execution_v3 import _event_candidate, eligible_rows
from independent_model_v24 import HistoricalMatch, LEAGUE_SOURCES
from model_v3 import apply_v3_forecasts, build_v3_forecasts, new_dynamic_state, update_dynamic_state
from strategy_v3 import decision_for_side
from validation_v3 import walk_forward_validate


class V3CoreTests(unittest.TestCase):
    @staticmethod
    def source(): return next(s for s in LEAGUE_SOURCES if s.key=="ENG-PL")

    @staticmethod
    def history(count=190):
        start=datetime(2024,7,1,tzinfo=timezone.utc); teams=("alpha","beta","gamma","delta","epsilon","zeta"); matches=[]
        for i in range(count):
            home=teams[i%len(teams)]; away=teams[(i*3+1)%len(teams)]
            if home==away: away=teams[(i+2)%len(teams)]
            hs=len(teams)-teams.index(home); as_=len(teams)-teams.index(away)
            hg=1+int(hs>as_)+int(i%4==0); ag=int(as_>=hs)+int(i%6==0)
            matches.append(HistoricalMatch(start+timedelta(days=i*3),"2425" if i<count//2 else "2526","ENG-PL",home,away,hg,ag))
        return matches

    @staticmethod
    def row(home_odds=2.0):
        row=engine.CombinedMatch(kickoff=datetime(2026,8,29,20,0,tzinfo=engine.BRISBANE),home_team="Alpha",away_team="Beta",sb_home=home_odds,sb_draw=3.5,sb_away=4.2,pm_fair_home=.5,pm_fair_draw=.27,pm_fair_away=.23); row.league="England Premier League"; return row

    def test_elo_initialisation_has_no_first_team_bonus(self):
        state=new_dynamic_state(self.source()); match=HistoricalMatch(datetime(2025,1,1,tzinfo=timezone.utc),"2526","ENG-PL","alpha","beta",2,0); update_dynamic_state(state,match)
        self.assertAlmostEqual(state.elo["alpha"]+state.elo["beta"],3000.0,places=10)

    def test_current_prices_do_not_change_v3_probability(self):
        history=self.history(); a=self.row(1.45); b=self.row(4.10); b.pm_fair_home,b.pm_fair_draw,b.pm_fair_away=.20,.25,.55
        fa=build_v3_forecasts([a],{"ENG-PL":history},bootstrap_samples=8)[a.match_name]; fb=build_v3_forecasts([b],{"ENG-PL":history},bootstrap_samples=8)[b.match_name]
        self.assertAlmostEqual(fa.home_probability,fb.home_probability,places=12); self.assertAlmostEqual(fa.draw_probability,fb.draw_probability,places=12); self.assertAlmostEqual(fa.away_probability,fb.away_probability,places=12)

    def test_only_genuinely_distinct_components_count(self):
        row=self.row(); f=build_v3_forecasts([row],{"ENG-PL":self.history()},bootstrap_samples=8)[row.match_name]
        self.assertEqual(f.components,("DYNAMIC-POISSON","ELO")); self.assertEqual(len(f.components),2); self.assertLessEqual(f.lower_home,f.home_probability); self.assertGreaterEqual(f.upper_home,f.home_probability)

    def test_ev_changes_with_price_not_probability(self):
        row=self.row(1.85); edge_model.calculate_match_edge(row,4.0); f=build_v3_forecasts([row],{"ENG-PL":self.history()},bootstrap_samples=8)[row.match_name]; apply_v3_forecasts([row],{row.match_name:f},4.0)
        first=decision_for_side(row,"HOME",4.0); self.assertIsNotNone(first); row.sb_home=2.30; second=decision_for_side(row,"HOME",4.0); self.assertIsNotNone(second); self.assertAlmostEqual(first.model_probability,second.model_probability,places=12); self.assertGreater(second.model_ev_pct,first.model_ev_pct)

    def test_all_modelled_rows_are_execution_targets(self):
        low,high=self.row(1.20),self.row(3.00); high.home_team,high.away_team="Gamma","Delta"
        for row in (low,high): row.model_fair_home,row.model_fair_draw,row.model_fair_away=.40,.30,.30
        self.assertEqual(len(eligible_rows([low,high])),2)

    def _event(self,target,offset):
        return {"startTime":(target.kickoff.astimezone(timezone.utc)+offset).isoformat(),"home":"Alpha FC","away":"Beta FC","markets":[{"canonicalMarket":"MATCH_RESULT","period":"FULL_TIME","selections":[{"canonicalOutcome":"HOME","odds":2.0},{"canonicalOutcome":"DRAW","odds":3.4},{"canonicalOutcome":"AWAY","odds":4.0}]}]}

    def test_strict_matching_rejects_eight_hours(self):
        target=self.row(); self.assertIsNone(_event_candidate(self._event(target,timedelta(hours=8)),target))

    def test_strict_matching_accepts_close_exact_fixture(self):
        target=self.row(); candidate=_event_candidate(self._event(target,timedelta(minutes=20)),target); self.assertIsNotNone(candidate); self.assertGreater(candidate[0],.93)

    def test_walk_forward_same_day_batch_does_not_leak_result(self):
        history=self.history(170); day=history[-1].kickoff+timedelta(days=3); m1=HistoricalMatch(day,"2526","ENG-PL","alpha","gamma",5,0); m2=HistoricalMatch(day,"2526","ENG-PL","beta","delta",1,1)
        a=history+[m1,m2]; b=history+[HistoricalMatch(day,"2526","ENG-PL","alpha","gamma",0,5),m2]
        ra=walk_forward_validate(self.source(),a,min_train_matches=120,fold_size=20,max_folds=1); rb=walk_forward_validate(self.source(),b,min_train_matches=120,fold_size=20,max_folds=1)
        pa=next(r for r in ra.records if r.home_team=="beta" and r.away_team=="delta"); pb=next(r for r in rb.records if r.home_team=="beta" and r.away_team=="delta")
        self.assertAlmostEqual(pa.p_home,pb.p_home,places=12); self.assertAlmostEqual(pa.p_draw,pb.p_draw,places=12); self.assertAlmostEqual(pa.p_away,pb.p_away,places=12)


if __name__=="__main__": unittest.main()
