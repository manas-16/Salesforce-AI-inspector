const fs = require('fs');
const path = require('path');
const code = fs.readFileSync(path.join(__dirname, 'sidebar.js'), 'utf8');
const match = code.match(/function renderMarkdown\([^\)]*\)[\s\S]*?function splitTextAndJsonSegments/);
if (!match) {
  throw new Error('no renderMarkdown match');
}
const fnSource = '(function(){' + match[0] + '\nreturn { renderMarkdown, splitTextAndJsonSegments, extractJsonSegments, renderJsonSegment, renderJsonData }; })()';
const { renderMarkdown, splitTextAndJsonSegments, extractJsonSegments, renderJsonSegment } = new Function(fnSource)();
const text = `**total_size:** 3 **returned:** 3

      {"total_size":3,"returned":3,"records":[
        {"Id":"500dL00003G1wDwQAJ","CaseNumber":"00001002","Subject":"Seeking guidance on electrical wiring installation for GC50","Status":"New","Priority":"Low","CreatedDate":"2026-05-29T14:18:57.000+0000"},
        {"Id":"500dL00003G1wEAQAZ00001016","CaseNumber":"00001016","Subject":"Maintenance guidelines for generator unclear","Status":"New","Priority":"Low","CreatedDate":"2026-05-29T14:18:57.000+0000"},
        {"Id":"500dL00003G1wEIQAZ00001024","CaseNumber":"00001024","Subject":"Design issue with mechanical rotor","Status":"New","Priority":"Low","CreatedDate":"2026-05-29T14:18:57.000+0000"}
      ]}

There are **3** open cases in the org.

| Case Number | Subject | Status | Priority | Created Date |
| --- | --- | --- | --- | --- |
| 00001002 | Seeking guidance on electrical wiring installation for GC50 | New | Low | 2026-05-29 |
`;
console.log('---- segments ----');
console.log(JSON.stringify(splitTextAndJsonSegments(text), null, 2));
console.log('---- rendered ----');
console.log(renderMarkdown(text));
