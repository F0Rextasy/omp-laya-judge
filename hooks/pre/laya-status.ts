import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";
import { version } from "../lib/record";

/**
 * laya-judge session banner: makes "laya active" visible on screen at startup.
 * No turn is triggered; the banner is display-only plus LLM-visible context
 * so the agent knows the local judge exists and when to use it. The banner,
 * like the decision layer and the live feed, is unconditional: there is no
 * flag that turns it off. The build stamp is on every decision card too, so
 * any card names the code that produced it.
 */
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
