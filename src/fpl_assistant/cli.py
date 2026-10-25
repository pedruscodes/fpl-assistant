"""Ligne de commande : `fpl ingest`, `pull`, `latest`, `baseline`, `train`, `recommend`,
`transfers`, `login`, `whoami`, `autopilot`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

from fpl_assistant import (
    DEFAULT_DATA,
    api,
    auth,
    autopilot,
    evaluate,
    features,
    ingest,
    model,
    recommend,
    report,
    transfers,
)


def _prepare(path: Path) -> pd.DataFrame:
    return features.prepare(pd.read_parquet(path))


def _report(label: str, test: pd.DataFrame, pred: pd.Series) -> None:
    # La majorité des lignes sont des joueurs qui ne jouent pas : on sépare les deux cas.
    played = test["minutes"] > 0
    m_all = evaluate.metrics(test["total_points"], pred)
    m_played = evaluate.metrics(test.loc[played, "total_points"], pred[played])
    top = evaluate.top_k_points(test, pred)
    print(
        f"{label:14} RMSE {m_all['rmse']:.3f}  MAE {m_all['mae']:.3f}  |  ont joué : "
        f"RMSE {m_played['rmse']:.3f}  MAE {m_played['mae']:.3f}  |  top 20 : {top:.2f} pts"
    )


def _login() -> None:

    def say(text: str = "") -> None:
        print(text, file=sys.stderr)

    login = auth.start_login()
    say("1. Ferme tes fenêtres de navigation privée, ouvre-en une nouvelle et connecte-toi à ton")
    say("   compte FPL avec cette adresse :\n")
    say(f"   {login.url}\n")
    say("2. Tu arrives sur fantasy.premierleague.com. Copie tout de suite l'adresse complète de")
    say("   la page : elle contient « code= » et ne vaut qu'une minute. Si elle a déjà changé,")
    say("   relance `fpl login`.")
    say("3. Ferme aussitôt la fenêtre privée. La FPL annule les jetons d'une session dès que le")
    say("   site s'y reconnecte : regarde ton équipe ailleurs (navigateur habituel, appli).\n")
    say("Adresse : ")
    tokens = auth.finish_login(login, input())
    # Un premier rafraîchissement vérifie que le jeton marche. La FPL en donne alors un
    # nouveau : c'est lui qu'on garde.
    renewed = auth.refresh(tokens["refresh_token"])
    token = renewed.get("refresh_token", tokens["refresh_token"])
    entry = api.FPLClient(access_token=renewed["access_token"]).me()["player"]["entry"]
    say(f"\nConnecté : équipe {entry}.")
    auth.save_refresh_token(token)
    say(f"Jeton enregistré dans {auth.TOKEN_FILE}.")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="fpl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Télécharge l'historique par journée")
    p_ingest.add_argument("--out", type=Path, default=DEFAULT_DATA)
    p_ingest.add_argument(
        "--strict", action="store_true", help="Échoue si une saison ne se télécharge pas"
    )

    p_pull = sub.add_parser("pull", help="Télécharge les données publiées par la GitHub Action")
    p_pull.add_argument("--out", type=Path, default=DEFAULT_DATA)
    sub.add_parser("latest", help="Dernière journée validée et version du format, ex. 2026-27:6:v1")
    p_rec = sub.add_parser("recommend", help="Équipe recommandée pour la prochaine journée")
    p_rec.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p_rec.add_argument("--budget", type=float, default=100.0, help="En M£ (défaut 100)")
    p_tr = sub.add_parser("transfers", help="Transferts conseillés depuis ton équipe FPL")
    p_tr.add_argument("--entry", type=int, required=True, help="ID d'équipe FPL (dans son URL)")
    p_tr.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p_tr.add_argument(
        "--free-transfers",
        type=int,
        choices=range(transfers.MAX_FREE_TRANSFERS + 1),
        metavar=f"0-{transfers.MAX_FREE_TRANSFERS}",
        help="Corrige l'estimation des transferts gratuits",
    )
    p_tr.add_argument(
        "--max-transfers",
        type=int,
        choices=range(16),
        metavar="0-15",
        help="Nombre maximum de transferts",
    )

    sub.add_parser("login", help="Connecte le dépôt à ton compte FPL")
    sub.add_parser("whoami", help="Vérifie la connexion au compte et affiche l'ID d'équipe")
    p_auto = sub.add_parser(
        "autopilot", help="Règle ton équipe pour la prochaine journée (connexion requise)"
    )
    p_auto.add_argument("--entry", type=int, help="ID d'équipe FPL (défaut : celle du compte)")
    p_auto.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p_auto.add_argument("--apply", action="store_true", help="Envoie vraiment à la FPL")
    p_auto.add_argument(
        "--max-hits",
        type=int,
        choices=range(16),
        metavar="0-15",
        default=autopilot.MAX_HITS,
        help=f"Transferts payants (-4) au plus (défaut {autopilot.MAX_HITS})",
    )
    p_auto.add_argument("--report", type=Path, help="Écrit le compte rendu en Markdown")
    p_auto.add_argument("--gw", type=int, help="N'envoie rien si ce n'est plus la prochaine")

    for name, help_ in [
        ("baseline", "Évalue la baseline forme récente"),
        ("train", "Entraîne LightGBM et le compare à la baseline"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--data", type=Path, default=DEFAULT_DATA)
        p.add_argument("--test-season", default="2025-26")
        p.add_argument("--test-from-gw", type=int, default=20)

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        print(f"Écrit : {ingest.ingest(out=args.out, strict=args.strict)}")
        return
    if args.cmd == "pull":
        try:
            print(f"Écrit : {ingest.pull(out=args.out)}")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return
    if args.cmd == "latest":
        try:
            print(ingest.marker(api.FPLClient().bootstrap()))
        except ValueError as exc:  # Rien à signaler : la sortie standard reste vide.
            print(exc, file=sys.stderr)
        return

    if args.cmd == "login":
        try:
            _login()
        except (RuntimeError, requests.RequestException) as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.cmd == "whoami":
        try:
            print(api.FPLClient(access_token=auth.access_token()).me()["player"]["entry"])
        except (RuntimeError, requests.RequestException) as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.cmd in ("recommend", "transfers", "autopilot"):
        try:
            history = pd.read_parquet(args.data)
            if args.cmd == "recommend":
                next_gw, squad = recommend.recommend(history, budget=args.budget)
            elif args.cmd == "transfers":
                plan = recommend.plan_transfers(
                    history,
                    args.entry,
                    free_transfers=args.free_transfers,
                    max_transfers=args.max_transfers,
                )
            else:
                client = api.FPLClient(access_token=auth.access_token())
                entry = args.entry or client.me()["player"]["entry"]
                result = autopilot.run(
                    history, entry, client, apply=args.apply, max_hits=args.max_hits, gw=args.gw
                )
        except FileNotFoundError:
            parser.exit(1, f"Pas de données dans {args.data} : lance `fpl pull`.\n")
        except ModuleNotFoundError as exc:
            parser.exit(1, f"{exc} : installe l'extra ML (pip install -e '.[ml]').\n")
        except requests.HTTPError as exc:
            parser.exit(1, f"Refus de l'API FPL : {exc}\n")
        except requests.RequestException as exc:
            parser.exit(1, f"API FPL injoignable : {exc}\n")
        except (ValueError, RuntimeError) as exc:
            parser.exit(1, f"{exc}\n")
        if args.cmd == "recommend":
            print("\n".join(report.squad_lines(f"Équipe recommandée pour la J{next_gw}", squad)))
        elif args.cmd == "transfers":
            lines = report.transfer_lines(
                plan, estimated_free=args.free_transfers is None, estimated_prices=True
            )
            print("\n".join(lines))
        else:
            print(result.markdown())
            if args.report:
                args.report.write_text(result.markdown())
        return

    gw = _prepare(args.data)
    train, test = evaluate.temporal_split(gw, args.test_season, args.test_from_gw)
    if test.empty or train.empty:
        parser.error(f"Split vide pour {args.test_season} à partir de la J{args.test_from_gw}")
    print(f"Test : {args.test_season} à partir de la J{args.test_from_gw}, {len(test)} lignes")
    for w in features.WINDOWS:
        _report(f"forme {w} J", test, evaluate.baseline_predict(test, w))
    if args.cmd == "train":
        _report("LightGBM", test, model.predict(model.train(train), test))


if __name__ == "__main__":
    main()
