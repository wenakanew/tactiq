import { CONTENT_LANGUAGES } from "../../lib/workspace-types";

export default function WorkspacePreferences({ audience, onAudience, language, onLanguage, player, onPlayer, players, teams }: {
  audience: string; onAudience: (mode: "fan" | "analyst" | "player") => void;
  language: string; onLanguage: (language: string) => void;
  player: string; onPlayer: (id: string) => void;
  players: Array<{ player_id: string; team_id: string | null; display_name?: string | null }>;
  teams: Record<string, string>;
}) {
  return (
    <div className="studio-context" aria-label="Personalization">
      <div className="context-group"><span className="context-label" id="perspective-label">Perspective</span>
        <div className="segmented" role="group" aria-labelledby="perspective-label">
          {(["fan", "analyst", "player"] as const).map((mode) => <button type="button" key={mode} aria-pressed={audience === mode}
            onClick={() => onAudience(mode)}>{mode === "fan" ? "Fan" : mode === "analyst" ? "Analyst" : "Player"}</button>)}
        </div>
      </div>
      {audience === "player" && players.length ? <div className="context-group context-player">
        <label className="context-label" htmlFor="player-pick">Focus player</label>
        <select id="player-pick" value={player} onChange={(event) => onPlayer(event.target.value)}>
          {!players.some((item) => item.player_id === player) ? <option value={player}>{player}</option> : null}
          {players.map((item) => <option key={item.player_id} value={item.player_id}>{item.team_id ? teams[item.team_id] ?? item.team_id : "Unassigned"} · {item.display_name ?? item.player_id}</option>)}
        </select>
      </div> : null}
      <div className="context-group"><label className="context-label" htmlFor="content-language">Language</label>
        <select id="content-language" className="language-select" value={language} onChange={(event) => onLanguage(event.target.value)}>
          {CONTENT_LANGUAGES.map(([locale, name]) => <option key={locale} value={locale}>{name}</option>)}
        </select>
      </div>
    </div>
  );
}
