import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

/**
 * laya-judge session banner: makes "laya aktif" visible on screen at startup.
 * No turn is triggered; the banner is display-only plus LLM-visible context
 * so the agent knows the local judge exists and when to use it.
 */
export default function hook(pi: HookAPI): void {
	pi.on("session_start", () => {
		pi.sendMessage({
			customType: "laya-judge",
			display: true,
			content:
				"● laya aktif — local System-1 judge (choice/bool/score, mean 160ms, 0 tokens). " +
				"Use mcp__laya-judge__judge for cheap classifications; escalate to the LLM " +
				"when confidence < 0.6. `/laya-judge:laya` runs a live status check.",
		});
	});
}
