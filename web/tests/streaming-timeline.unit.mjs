import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const source = fs.readFileSync(
  path.join(root, "lib", "streamingTimeline.ts"),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
});

const compiledModule = { exports: {} };
vm.runInNewContext(compiled.outputText, {
  module: compiledModule,
  exports: compiledModule.exports,
  require,
});

const {
  appendTimelineText,
  upsertTimelineToolStart,
  upsertTimelineToolResult,
} = compiledModule.exports;

function toolCall(id, name = "bash") {
  return {
    tool_call_id: id,
    tool_name: name,
    input: { command: "pwd" },
    status: "running",
  };
}

{
  let timeline = [];
  let nextSeq = 0;
  ({ timeline, nextSeq } = appendTimelineText(timeline, "先读取", nextSeq, 1));
  ({ timeline, nextSeq } = upsertTimelineToolStart(timeline, toolCall("t1"), nextSeq, 2));
  ({ timeline, nextSeq } = upsertTimelineToolResult(
    timeline,
    {
      tool_call_id: "t1",
      tool_name: "bash",
      output: "ok",
      is_error: false,
    },
    nextSeq,
    3,
  ));
  ({ timeline, nextSeq } = appendTimelineText(timeline, "读取完成", nextSeq, 4));

  assert.equal(timeline.map((item) => item.kind).join(","), "text,tool,text");
  assert.equal(timeline[0].content, "先读取");
  assert.equal(timeline[1].status, "completed");
  assert.equal(timeline[2].content, "读取完成");
}

{
  let timeline = [];
  let nextSeq = 0;
  ({ timeline, nextSeq } = appendTimelineText(timeline, "A", nextSeq, 1));
  ({ timeline, nextSeq } = appendTimelineText(timeline, "B", nextSeq, 2));
  ({ timeline, nextSeq } = upsertTimelineToolStart(timeline, toolCall("t2", "file_read"), nextSeq, 3));
  ({ timeline, nextSeq } = appendTimelineText(timeline, "C", nextSeq, 4));

  assert.equal(timeline.map((item) => item.kind).join(","), "text,tool,text");
  assert.equal(timeline[0].content, "AB");
  assert.equal(timeline[2].content, "C");
}

{
  let timeline = [];
  let nextSeq = 0;
  ({ timeline, nextSeq } = upsertTimelineToolStart(timeline, toolCall("t3"), nextSeq, 1));
  ({ timeline, nextSeq } = upsertTimelineToolResult(
    timeline,
    {
      tool_call_id: "t3",
      tool_name: "bash",
      output: "done",
      is_error: false,
    },
    nextSeq,
    2,
  ));

  assert.equal(timeline.length, 1);
  assert.equal(timeline[0].kind, "tool");
  assert.equal(timeline[0].status, "completed");
  assert.equal(timeline[0].output, "done");
}

console.log("streaming timeline unit tests passed");
