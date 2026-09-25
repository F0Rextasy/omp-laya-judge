import { readFileSync } from "node:fs";
import * as path from "node:path";
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

/**
 * laya-judge session banner: makes "laya active" visible on screen at startup.
 * No turn is triggered; the banner is display-only plus LLM-visible context
 * so the agent knows the local judge exists and when to use it. The banner,
 * like the decision layer and the live feed, is unconditional: there is no
 * flag that turns it off.
 *
 * The version is read from the manifest on every start, because hooks are
 * loaded once per omp process: a window opened before an update keeps the
 * old code in memory, and the stamp is what tells the two apart on screen.
 */
function version(): string {
	try {
		const manifest = readFileSync(path.resolve(import.meta.dir, "../../package.json"), "utf-8");
		const parsed = JSON.parse(manifest) as { version?: string };
		return parsed.version ?? "dev";
	} catch {
		return "dev";
	}
}

export default function hook(pi: HookAPI): void {
	pi.on("session_start", () => {
		pi.sendMessage({
			customType: "laya-judge",
			display: true,
			content:
				`● laya active v${version()} — local System-1 judge (choice/bool/score, mean 160ms, 0 tokens). ` +
				"Use mcp__laya-judge__judge for cheap classifications; escalate to the LLM " +
				"when confidence < 0.6. `/laya-judge:laya` runs a live status check. " +
				"The harness decision layer and live feed are always on.",
		});
	});
}
