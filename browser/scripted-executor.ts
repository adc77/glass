import type { ExecutorNode, StepExecutor } from "e2e";

export const scripted: StepExecutor = {
  name: "glass-scripted-browser-not-LLM",
  version: "1",
  cache: "off",
  async runStep(ctx) {
    const params = ctx.step.params;
    async function target(role: string, name: string) {
      const observation = await ctx.observe({ tree: true });
      const found: ExecutorNode[] = [];
      function visit(node: ExecutorNode | undefined) {
        if (!node) return;
        if (node.role === role && node.name === name && !node.states?.disabled)
          found.push(node);
        for (const child of node.children ?? []) visit(child);
      }
      visit(observation.tree);
      const [node] = found;
      if (!node || found.length !== 1)
        throw new Error(
          `Expected one enabled ${role} named ${name}, got ${found.length}`,
        );
      return { id: node.id };
    }
    if (ctx.step.kind !== "act")
      return {
        status: "blocked",
        errorCode: "AUTOMATION_UNSUPPORTED",
        summary: "Scripted driver has no model or semantic judge.",
      };
    switch (ctx.step.instruction) {
      case "Start capture":
      case "Finish capture":
        await ctx.actions.tap(await target("button", ctx.step.instruction));
        break;
      case "Receive sample":
        if (typeof params?.sample !== "string")
          throw new Error("Explicit sample parameter required");
        await ctx.actions.tap(await target("button", "New sample"));
        await ctx.actions.type(
          await target("textbox", "Sample name"),
          params.sample,
        );
        await ctx.actions.tap(await target("button", "Receive sample"));
        break;
      case "Record reading":
        if (
          !params ||
          typeof params.value !== "number" ||
          !Number.isInteger(params.value)
        )
          throw new Error("Explicit integer reading required");
        await ctx.actions.type(
          await target("spinbutton", "Reading value"),
          String(params.value),
        );
        await ctx.actions.tap(await target("button", "Record reading"));
        break;
      default:
        return {
          status: "blocked",
          errorCode: "AUTOMATION_UNSUPPORTED",
          summary: "Unknown scripted operation; no LLM fallback.",
        };
    }
    return {
      status: "passed",
      summary:
        "Scripted UI actions dispatched; independent locator assertions verify actual outcomes.",
    };
  },
};
