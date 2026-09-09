/* TRIZ-GPT 前端：Vue 3 单页应用 */
const { createApp } = Vue;

/* 轻量 SVG 折线图（凸包体积 / 理想性迭代曲线） */
const SvgLine = {
  props: { points: { type: Array, default: () => [] }, color: { type: String, default: "#2563eb" } },
  template: `
    <svg :viewBox="'0 0 300 90'" class="svg-line" preserveAspectRatio="none">
      <line v-for="g in 4" :key="g" :x1="0" :x2="300" :y1="g*18" :y2="g*18"
            stroke="#e8e0cf" stroke-width="1" stroke-dasharray="4 4"/>
      <polyline :points="path" fill="none" :stroke="color" stroke-width="2.5"
                stroke-linejoin="round" stroke-linecap="round"/>
      <circle v-for="(p,i) in pts" :key="i" :cx="p.x" :cy="p.y" r="3.5" :fill="color"/>
    </svg>`,
  computed: {
    pts() {
      const vals = this.points.map(Number);
      if (vals.length === 0) return [];
      const max = Math.max(...vals), min = Math.min(...vals);
      const span = max - min || 1;
      const n = vals.length;
      return vals.map((v, i) => ({
        x: n === 1 ? 150 : 12 + (i * 276) / (n - 1),
        y: 78 - ((v - min) / span) * 66,
      }));
    },
    path() {
      return this.pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    },
  },
};

createApp({
  components: { SvgLine },
  data() {
    return {
      view: "home",
      problem: "",
      autoMode: false,
      candidatesK: 6,
      optIterN: 4,
      examples: [
        { name: "起落架：强度 vs 重量",
          text: "飞机起落架在起降时需要承受巨大的冲击载荷，因此必须做得足够坚固、强度高；但越坚固的起落架往往越重，导致飞机整体重量增加、能耗上升。如何在保证起落架结构强度的同时，控制其重量与能耗？" },
        { name: "雨伞：遮挡面积 vs 便携性",
          text: "雨伞需要足够大的遮挡面积来遮风挡雨，但伞面越大，雨伞越重、越难携带，遇到大风时也越容易被吹翻损坏。如何让雨伞在保证遮挡效果的同时，保持轻便、易携带且抗风？" },
        { name: "保温杯：隔热 vs 容量重量",
          text: "保温杯需要良好的隔热保温性能，通常杯壁要做得很厚并采用多层真空结构；但厚杯壁导致杯子笨重、容量减小且握持不便。如何在提升保温效果的同时，让杯子轻便且容量充足？" },
      ],
      phases: [
        { index: 0, name: "识别矛盾", desc: "提取参数、匹配TRIZ矛盾、查询发明原则",
          team: ["analyst", "matcher", "methodologist"] },
        { index: 1, name: "方案生成", desc: "依据发明原则发散生成候选方案",
          team: ["engineer"] },
        { index: 2, name: "方案评估", desc: "收益/成本/副作用理想性评分收拢",
          team: ["expert_benefit", "expert_cost", "expert_harm"] },
        { index: 3, name: "优化迭代", desc: "专家建议驱动方案微调优化",
          team: ["refiner", "expert_benefit", "expert_cost", "expert_harm"] },
      ],
      // 进度轨道节点：TRIZ 认知链路 × 发散/收敛过程，每个节点可锚定到对话流对应位置
      // kind：dot=链路节点，diamond=发散（一到多），ring=收敛（多到一），loop=迭代循环
      // anchor：节点在对话流中的锚点（主锚点 + 备选）
      progressSteps: [
        { key: "params", short: "问题参数", kind: "dot",
          title: "问题参数 · 问题参数提取师从描述中抽取工程参数",
          anchors: ["phase0", "agent:analyst"] },
        { key: "contra", short: "矛盾参数", kind: "dot",
          title: "矛盾参数 · 匹配 39 个工程参数，识别技术矛盾对",
          anchors: ["agent:matcher", "phase0"] },
        { key: "pair", short: "矛盾选择", kind: "dot",
          title: "矛盾选择 · 人工选定主要矛盾对（可编辑参数 / 分叉探索）",
          anchors: ["pause:pair"] },
        { key: "principle", short: "原则选择", kind: "dot",
          title: "原则选择 · 矛盾矩阵查表 / 方法学家推断发明原则（40 原则）",
          anchors: ["agent:methodologist", "pause:pair"] },
        { key: "diverge", short: "方案发散", kind: "diamond",
          title: "方案发散 · 依据发明原则发散生成多个候选方案（一到多）",
          anchors: ["phase1", "agent:engineer"] },
        { key: "converge", short: "方案收敛", kind: "ring",
          title: "方案收敛 · 收益 / 成本 / 副作用评审评分，人工定夺基线（多到一）",
          anchors: ["pause:candidate"] },
        { key: "iterate", short: "方案迭代", kind: "loop",
          title: "方案迭代 · 评审-精化循环，逐轮提升理想性",
          anchors: ["pause:iterate"] },
        { key: "final", short: "方案完成", kind: "star",
          title: "方案完成 · 全流程结束，最终方案落定",
          anchors: ["final-sys", "pause:iterate", "pause:candidate"] },
      ],
      roster: [
        { key: "analyst", icon: "🔍", title: "问题参数提取师",
          duty: "从问题描述中抽取希望改善与可能恶化的工程参数", action: "正在阅读问题，抽取关键工程参数" },
        { key: "matcher", icon: "🧭", title: "TRIZ参数匹配专家",
          duty: "将具体参数匹配到39个工程参数，识别技术矛盾对", action: "正在匹配工程参数，识别技术矛盾" },
        { key: "methodologist", icon: "📚", title: "TRIZ方法学家",
          duty: "矛盾矩阵无推荐时，从40个发明原则中推断适用原则", action: "正在推断适用的发明原则" },
        { key: "engineer", icon: "💡", title: "创意设计师",
          duty: "依据发明原则发散生成具体、可落地的候选方案", action: "正在运用发明原则构思候选方案" },
        { key: "expert_benefit", icon: "📈", title: "收益评审专家",
          duty: "评估方案的有用功能与收益大小", action: "正在评估方案收益" },
        { key: "expert_cost", icon: "⚖️", title: "成本评审专家",
          duty: "评估结构、材料、制造、能耗等成本代价", action: "正在评估方案成本" },
        { key: "expert_harm", icon: "🛡️", title: "副作用评审专家",
          duty: "评估方案可能引入的有害副作用", action: "正在评估有害副作用" },
        { key: "refiner", icon: "🔧", title: "优化设计师",
          duty: "依据评审建议对方案做微调优化", action: "正在依据评审建议微调优化方案" },
      ],
      phase: -1,
      messages: [],
      activeAgent: null,
      streaming: null,
      params: null,
      pairs: [],
      selectedPair: null,
      principles: [],
      principleSource: "matrix",
      candidates: [],
      hullSeries: [],
      hullRatio: 1,
      scores: [],
      bestIndex: -1,
      optimizations: [],
      final: null,
      error: "",
      paused: false,
      running: false,
      ws: null,
      currentRunId: "",   // 当前查看的会话 id（分支切换时变化）
      branches: [],       // 当前探索家族（根 run + 全部分支），供分支树展示
      treeCollapsed: false, // 左上角分支树折叠态
      pendingLocate: null,  // 切换分支后待定位目标：轨道节点索引（0..6）或 "latest"（最新断点）
      // TRIZ 知识字典（39 参数 + 40 原则的阐述与例子），供悬浮气泡查询
      kn: { parameter: {}, principle: {} },
      knLoaded: false,
      tip: { show: false, x: 0, y: 0, title: "", detail: "", examples: [] },
      // 多分支进度轨道：分支行悬停气泡（展示分支名/状态/进度/轮次/分叉点）
      bpTip: { show: false, x: 0, y: 0, title: "", lines: [] },
    };
  },
  computed: {
    rankedScores() {
      return [...this.scores].sort((a, b) => b.ideality - a.ideality);
    },
    // 39 个工程参数目录（矛盾对编辑/自定义时下拉选择用）
    paramOptions() {
      return Object.values(this.kn.parameter || {})
        .map(p => ({ id: p.id, name: p.name }))
        .sort((a, b) => a.id - b.id);
    },
    // 当前查看的分支节点（折叠态标题栏展示其名称与状态）
    currentBranch() {
      return this.branches.find(b => b.run_id === this.currentRunId) || null;
    },
    // 根分支（初始路径）：轨道第一行直接复用其 8 个节点，不再另起行
    rootBranch() {
      return this.branches.find(b => !b.parent_run_id) || null;
    },
    // 子分支行（根分支独占第一行，其余按家族序在下方）
    childBranches() {
      return this.branches.filter(b => b.parent_run_id);
    },
    // 创意方案卡片：候选方案与评估结果按 index 合并，供右侧横向卡轨展示
    candCards() {
      return this.candidates.map(c => {
        const score = this.scores.find(s => s.index === c.index) || null;
        return { ...c, score, isBest: this.bestIndex === c.index };
      });
    },
    // 优化迭代时间轴：按轮次正序（存储为最新在前，展示从左到右）
    optTimeline() {
      return [...this.optimizations].sort((a, b) => a.iteration - b.iteration);
    },
    // 理想性迭代曲线：取最后一轮携带的完整序列（初始最优 → 各轮）
    optSeries() {
      const last = this.optTimeline[this.optTimeline.length - 1];
      return last && last.series ? last.series.map(s => s.ideality) : [];
    },
    hasAnyResult() {
      return !!(this.final || this.error || this.params || this.pairs.length
        || this.selectedPair || this.principles.length || this.candidates.length
        || this.scores.length || this.optimizations.length);
    },
    activeAction() {
      if (!this.activeAgent) return "";
      if (this.activeAgent.agent === "panel") return "三位评审专家正在并行打分";
      const found = this.roster.find(r => r.key === this.activeAgent.agent);
      return found ? found.action : "正在工作中";
    },
  },
  mounted() {
    const qs = new URLSearchParams(location.search);
    const ck = Number(qs.get("candidates_k"));
    if (Number.isFinite(ck) && ck >= 3 && ck <= 9) this.candidatesK = ck;
    const on = Number(qs.get("opt_iter_n"));
    if (Number.isFinite(on) && on >= 1 && on <= 6) this.optIterN = on;
    const ex = qs.get("example");
    if (ex === "umbrella") this.problem = this.examples[1]?.text || "";
    // 深链：?run=<run_id> 直接打开指定会话（分支回看 / 链接分享）
    const runId = qs.get("run");
    if (runId) {
      this.view = "workspace";
      this.openRun(runId, "latest");
    }
    if (qs.get("view") === "workspace") this.view = "workspace";
    if (qs.get("autorun") === "1") {
      this.autoMode = true;
      this.$nextTick(() => {
        if (!this.problem.trim()) this.problem = this.examples[1]?.text || "";
        if (this.problem.trim()) this.startRun();
      });
    }
    this.loadKnowledge();
    this.bindKnowledgeTip();
  },
  methods: {
    // 加载 TRIZ 知识字典（39 参数 + 40 原则），失败静默降级（悬浮气泡不显示）
    loadKnowledge() {
      fetch("/api/triz/knowledge").then(r => r.json()).then(d => {
        const kn = { parameter: {}, principle: {} };
        (d.parameters || []).forEach(p => { kn.parameter[p.id] = p; });
        (d.principles || []).forEach(p => { kn.principle[p.id] = p; });
        this.kn = kn;
        this.knLoaded = true;
      }).catch(() => {});
    },
    // 全局事件委托：悬停任意 [data-k] 热区时显示知识气泡（参数/原则的阐述与例子）
    bindKnowledgeTip() {
      let anchor = null;   // 当前悬停的热区元素
      let hideTimer = null;
      const hot = (node) => (node && node.closest) ? node.closest("[data-k]") : null;
      const showTip = (el) => {
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        const type = el.getAttribute("data-k");
        const id = el.getAttribute("data-id");
        const info = (this.kn[type] || {})[id];
        if (!info) return;
        anchor = el;
        const r = el.getBoundingClientRect();
        this.tip = {
          show: true,
          x: r.left, y: r.bottom + 6,
          title: type === "parameter"
            ? `参数 K${id} · ${info.name}`
            : `原则 ${id} · ${info.name}`,
          detail: info.detail || info.desc || "",
          examples: info.examples || [],
        };
        this.$nextTick(() => this.clampTip(r));
      };
      const scheduleHide = () => {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(() => { anchor = null; this.tip.show = false; }, 120);
      };
      // 进入热区：仅当悬停目标切换到新的热区时才重建气泡（热区内部移动不重复触发）
      document.addEventListener("mouseover", (e) => {
        const el = hot(e.target);
        if (!el) return;
        if (el !== anchor) showTip(el);
        else if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      }, true);
      // 离开热区：只有从当前热区移到其外部（非同一热区）时才安排隐藏
      document.addEventListener("mouseout", (e) => {
        const from = hot(e.target);
        if (!from || from !== anchor) return;
        if (hot(e.relatedTarget) === from) return;
        scheduleHide();
      }, true);
      // 滚动时气泡跟随热区重新定位（流式输出自动滚动不再导致闪现）；热区已被 DOM 替换才隐藏
      document.addEventListener("scroll", () => {
        if (!this.tip.show || !anchor) return;
        if (!anchor.isConnected) { anchor = null; this.tip.show = false; return; }
        const r = anchor.getBoundingClientRect();
        this.tip.x = r.left;
        this.tip.y = r.bottom + 6;
        this.clampTip(r);
      }, true);
      document.addEventListener("click", () => { anchor = null; this.tip.show = false; }, true);
    },
    // 视口边界修正：防止浮层超出右/下边界，空间不足时翻转到热区上方
    clampTip(rect) {
      const el = document.getElementById("kn-tip");
      if (!el) return;
      const w = el.offsetWidth, h = el.offsetHeight;
      let x = rect.left;
      if (x + w > window.innerWidth - 10) x = window.innerWidth - w - 10;
      if (x < 8) x = 8;
      let y = rect.bottom + 6;
      if (y + h > window.innerHeight - 8) y = rect.top - h - 6;
      if (y < 8) y = 8;
      this.tip.x = x;
      this.tip.y = y;
    },
    avatarOf(agent) {
      return {
        analyst: "🕵️", matcher: "🧑‍🔬", engineer: "🧑‍🎨",
        expert_benefit: "🧑‍💼", expert_cost: "👷", expert_harm: "🧑‍🚒",
        refiner: "🧑‍🔧", methodologist: "🧑‍🏫", panel: "👥",
      }[agent] || "👤";
    },
    rosterItem(key) {
      return this.roster.find(r => r.key === key)
        || { key, icon: "🤖", title: key, duty: "" };
    },
    renderText(text) {
      const div = document.createElement("div");
      div.textContent = text || "";
      return div.innerHTML.replace(/\n/g, "<br>");
    },
    restart() {
      if (this.ws) this.ws.close();
      this.view = "home";
      this.resetState();
    },
    resetState() {
      this.phase = -1; this.messages = []; this.params = null; this.pairs = [];
      this.selectedPair = null; this.principles = []; this.candidates = [];
      this.hullSeries = []; this.hullRatio = 1; this.scores = [];
      this.bestIndex = -1; this.optimizations = []; this.final = null;
      this.error = ""; this.paused = false;
      this.running = false; this.activeAgent = null;
      this.streaming = null; this.pendingLocate = null;
    },
    scrollDocToFinal() {
      // 最终方案位于右侧文档最底部：完成时滚动到文末
      this.$nextTick(() => {
        const doc = document.querySelector(".doc-scroll");
        if (doc) doc.scrollTop = doc.scrollHeight;
      });
    },
    async startRun() {
      this.resetState();
      this.running = true;
      this.view = "workspace";
      const resp = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem: this.problem,
          auto_mode: this.autoMode,
          candidates_k: this.candidatesK,
          opt_iter_n: this.optIterN,
        }),
      });
      const { run_id } = await resp.json();
      this.connectRun(run_id);
      this.refreshBranches();
    },
    // 打开（切换到）一个已有会话：回放历史 + 接收实时流（分支切换/历史回看共用）
    // locate：回放后自动定位的目标——"latest"=最新断点，或轨道节点索引（0..6），或阶段名
    openRun(runId, locate = "latest") {
      const stageToNode = { pair: 2, candidate: 5, iterate: 6 };
      const target = (typeof locate === "string" && stageToNode[locate]) ? stageToNode[locate] : locate;
      if (runId === this.currentRunId) {
        this.locateInMessages(target);
        return;
      }
      if (this.ws) this.ws.close();
      this.resetState();
      this.pendingLocate = target;
      this.view = "workspace";
      this.running = true;
      this.connectRun(runId);
      this.refreshBranches();
    },
    connectRun(runId) {
      this.currentRunId = runId;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`);
      this.ws.onmessage = (e) => this.handleEvent(JSON.parse(e.data));
      this.ws.onclose = () => { this.running = false; this.refreshBranches(); };
    },
    // 拉取会话列表，计算当前 run 所属探索家族（根 + 全部分支）供分支轨展示
    async refreshBranches() {
      try {
        const resp = await fetch("/api/runs");
        const runs = await resp.json();
        const byId = {};
        runs.forEach(r => { byId[r.run_id] = r; });
        let rootId = this.currentRunId;
        let guard = 0;
        while (byId[rootId] && byId[rootId].parent_run_id && guard++ < 30) {
          rootId = byId[rootId].parent_run_id;
        }
        const family = [];
        const walk = (id, depth) => {
          const node = byId[id];
          if (!node) return;
          family.push({ ...node, depth });
          runs.filter(r => r.parent_run_id === id)
              .sort((a, b) => a.created_at - b.created_at)
              .forEach(c => walk(c.run_id, depth + 1));
        };
        walk(rootId, 0);
        // 进度轨道行数据：分叉列（子分支进度线起点）、父行行号（分叉竖线连接）
        const rowOf = {};
        family.forEach((node, i) => {
          node.rowIndex = i;
          rowOf[node.run_id] = i;
          // 分叉阶段 → 轨道节点列：pair=矛盾选择(2) / candidate=方案收敛(5) / iterate=方案迭代(6)
          node.forkCol = node.parent_run_id
            ? ({ pair: 2, candidate: 5, iterate: 6 }[node.fork_stage] ?? null)
            : null;
          node.parentRow = node.parent_run_id ? rowOf[node.parent_run_id] : null;
          // 点击进度线时定位：子分支定位到其分叉断点，根分支定位到最新断点
          node.locateStage = node.parent_run_id ? (node.fork_stage || "latest") : "latest";
        });
        // 子分支按创建先后全局编号（分支1、分支2…），编号不随行序/分叉阶段变化
        family.filter(n => n.parent_run_id)
          .slice()
          .sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
          .forEach((n, i) => { n.branchNo = i + 1; });
        this.branches = family;
      } catch (e) {
        this.branches = [];
      }
    },
    branchLabel(b) {
      // 根分支 = 穿过全部节点的初始路径；子分支按创建顺序编号（分支1、分支2…）
      if (!b.parent_run_id) return "🌱 初始路径";
      return `⎿ 分支${b.branchNo || ""}`;
    },
    // ── 多分支进度轨道：8 个共享节点列，每条分支一条贯穿进度线 ──────────
    // 实时分支：从前端事件流推导当前 active 节点（0..7），8=全程完成
    liveStep() {
      if (this.final) return 8;
      // 最新的待决策暂停面板即当前断点
      for (let i = this.messages.length - 1; i >= 0; i--) {
        const m = this.messages[i];
        if (m.type === "pause" && !m.superseded && !m.done) {
          return { pair: 2, candidate: 5, iterate: 6 }[m.stage] ?? 0;
        }
      }
      if (this.phase === 3 || this.optimizations.length) return 6;
      if (this.phase === 2 || this.scores.length) return 5;
      if (this.phase === 1 || this.candidates.length || this.principles.length) return 4;
      if (this.selectedPair) return 3;
      if (this.pairs.length) return 2;
      if (this.params || this.phase === 0) return 1;
      return 0;
    },
    // 分支在轨道上的 active 节点索引：当前分支走实时推导，他分支走后端里程碑
    branchStep(b) {
      if (b && b.run_id === this.currentRunId && this.messages.length) return this.liveStep();
      return { starting: 0, pair: 2, principles: 4, diverged: 5,
               candidate: 5, iterate: 6, done: 8 }[(b && b.progress) || "starting"] ?? 0;
    },
    // 分支优化迭代轮次（迭代节点标注 ×N）
    branchIter(b) {
      let n = (b && b.iter_round) || 0;
      if (b && b.run_id === this.currentRunId) {
        this.messages.forEach(m => {
          if (m.type === "pause" && m.stage === "iterate" && !m.superseded && m.iter)
            n = Math.max(n, m.iter);
        });
      }
      return n;
    },
    // 段（列间连线）状态：done=已完成（绿）/ active=通向当前断点（金）/ pending=未到（灰）/ none=子分支前缀
    segClass(b, i) {
      const start = (b && b.forkCol != null) ? b.forkCol : 0;
      if (i < start) return "seg-none";
      const step = this.branchStep(b);
      if (step >= 8 || i + 1 < step) return "seg-done";
      if (i + 1 === step) return "seg-active";
      return "seg-pending";
    },
    segStyle(i) {
      return { left: ((i + 0.5) / 8 * 100) + "%", width: (100 / 8) + "%" };
    },
    // 节点标记状态：st-done / st-active / st-pending / mark-none（子分支前缀列不渲染）
    markClass(b, si) {
      const start = (b && b.forkCol != null) ? b.forkCol : 0;
      if (si < start) return "mark-none";
      const step = this.branchStep(b);
      if (step >= 8 || si < step) return "st-done";
      if (si === step) return "st-active";
      return "st-pending";
    },
    markStyle(si) {
      return { left: ((si + 0.5) / 8 * 100) + "%" };
    },
    nodeGlyph(b, si) {
      const start = (b && b.forkCol != null) ? b.forkCol : 0;
      if (si < start) return "";
      const step = this.branchStep(b);
      return (step >= 8 || si < step) ? "✓" : "";
    },
    // 分叉竖线：在分叉列位置从父行进度线连到本行起点（行高 34px，进度线心 23px）
    forkStyle(b) {
      const d = Math.max(1, b.rowIndex - (b.parentRow ?? b.rowIndex));
      const x = ((b.forkCol + 0.5) / 8) * 100;
      return { left: x + "%", top: (23 - d * 34) + "px", height: (d * 34) + "px" };
    },
    // 点击进度线：切换到该分支并定位到断点（已在当前分支则直接滚动）
    locateBranch(b) {
      this.bpTip.show = false;
      if (b.run_id === this.currentRunId) {
        this.locateInMessages(b.locateStage || "latest");
      } else {
        this.openRun(b.run_id, b.locateStage || "latest");
      }
    },
    // 点击轨道节点：锚定到该分支对话流中的对应位置（尚未到达的节点忽略）
    locateNode(b, si) {
      this.bpTip.show = false;
      if (!b || si > this.branchStep(b)) return;
      if (b.run_id === this.currentRunId) {
        this.locateInMessages(si);
      } else {
        this.openRun(b.run_id, si);
      }
    },
    // 节点锚点描述符（phase0/phase1/agent:xxx/pause:stage）→ 消息对象
    anchorMessage(nodeIdx) {
      const step = this.progressSteps[nodeIdx];
      if (!step) return null;
      for (const desc of step.anchors) {
        let m = null;
        if (desc.startsWith("agent:")) {
          const ag = desc.slice(6);
          m = this.messages.find(x => x.type === "msg" && x.agent === ag) || null;
        } else if (desc.startsWith("pause:")) {
          const st = desc.slice(6);
          const live = this.messages.filter(x => x.type === "pause" && x.stage === st && !x.superseded);
          m = live[live.length - 1]
            || this.messages.filter(x => x.type === "pause" && x.stage === st).pop() || null;
        } else if (desc === "phase0" || desc === "phase1") {
          const idx = Number(desc.slice(5));
          m = this.messages.find(x => x.type === "phase" && x.index === idx) || null;
        } else if (desc === "final-sys") {
          // 方案完成：done 时推送的完成系统消息（回放/实时均会到达）
          m = this.messages.find(x => x.type === "system" && x.kind === "final") || null;
        }
        if (m) return m;
      }
      return null;
    },
    // 在当前消息流中定位：target=轨道节点索引，或 "latest"=最新断点暂停面板
    locateInMessages(target) {
      let msg = null;
      if (target === "latest") {
        const live = this.messages.filter(m => m.type === "pause" && !m.superseded);
        msg = live[live.length - 1] || null;
      } else {
        msg = this.anchorMessage(target);
      }
      this.messages.forEach(m => { m.locateMark = false; });
      if (msg) {
        msg.locateMark = true;
        this.scrollToLocate();
      }
    },
    // 分支切换回放/实时流中：待定位锚点出现后打标记并滚动居中；返回是否已定位
    checkPendingLocate() {
      if (this.pendingLocate == null) return false;
      let msg = null;
      if (this.pendingLocate === "latest") {
        // 最新断点：跟随最近一个仍未决策的暂停面板（已决策/分叉前的不锚）
        const live = this.messages.filter(
          m => m.type === "pause" && !m.superseded && !m.done);
        msg = live[live.length - 1] || null;
      } else {
        msg = this.anchorMessage(this.pendingLocate);
      }
      if (!msg) return false;
      this.messages.forEach(m => { m.locateMark = false; });
      msg.locateMark = true;
      // 具体节点锚点命中即锁定；"latest" 持续跟随后续断点，回放结束（done）时释放
      if (this.pendingLocate !== "latest") this.pendingLocate = null;
      this.scrollToLocate();
      return true;
    },
    // 进度线悬停：整行高亮 + 气泡展示分支信息（名称/状态/进度/轮次/分叉点）
    onBpEnter(ev, b) {
      const r = ev.currentTarget.getBoundingClientRect();
      const step = this.branchStep(b);
      const stMap = { running: "🔄 运行中", done: "✅ 已完成", error: "⚠️ 运行异常" };
      const lines = [
        "状态：" + (stMap[b.status] || b.status),
        "进度：" + (step >= 8 ? "全程完成"
          : "停在「" + this.progressSteps[Math.min(step, 7)].short + "」节点"),
      ];
      const it = this.branchIter(b);
      if (it) lines.push("优化迭代：" + it + " 轮");
      if (b.forkCol != null) {
        const parent = this.branches[b.parentRow];
        const fName = { 2: "矛盾选择", 5: "方案收敛", 6: "方案迭代" }[b.forkCol];
        lines.push("分叉点：" + fName + (parent ? "（自「" + this.branchLabel(parent) + "」分出）" : ""));
      }
      lines.push("─────");
      lines.push("点击进度线切换分支；点击节点定位对话位置");
      this.bpTip = { show: true, title: this.branchLabel(b), lines, x: 0, y: 0 };
      this.$nextTick(() => {
        const el = document.getElementById("bp-tip");
        if (!el) return;
        const w = el.offsetWidth, h = el.offsetHeight;
        let x = r.left;
        if (x + w > window.innerWidth - 8) x = window.innerWidth - w - 8;
        if (x < 8) x = 8;
        let y = r.bottom + 6;
        if (y + h > window.innerHeight - 8) y = r.top - h - 6;
        if (y < 8) y = 8;
        this.bpTip.x = x; this.bpTip.y = y;
      });
    },
    onBpLeave() { this.bpTip.show = false; },
    // 平滑滚动到带定位标记的消息行（暂停面板 / 角色气泡 / 阶段分隔条）并闪烁提示
    scrollToLocate() {
      this.$nextTick(() => {
        const el = document.querySelector(".dialogue .msg-row[data-locate]");
        if (!el) return;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.remove("locate-flash");
        void el.offsetWidth;  // 重置动画
        el.classList.add("locate-flash");
      });
    },
    // 从检查点开新分支：父分支保持暂停，子分支在分叉阶段重新等待决策
    async forkRun(stage) {
      if (!this.currentRunId) return;
      const resp = await fetch(`/api/runs/${this.currentRunId}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        alert(err.detail || "分叉失败，请稍后重试");
        return;
      }
      const { run_id } = await resp.json();
      this.openRun(run_id, stage);  // 新分支直接定位到分叉断点
    },
    activePause() {
      // 瀑布流中最后一个待决策的人机交互节点
      for (let i = this.messages.length - 1; i >= 0; i--) {
        const m = this.messages[i];
        if (m.type === "pause" && !m.done) return m;
      }
      return null;
    },
    resolvePause(decisionLabel) {
      // 交互节点就地闭合：折叠为结果行，新内容只在其下游追加
      const m = this.activePause();
      if (!m) return;
      m.done = true;
      // 用户做出决策即释放定位锚点，视图恢复跟随实时流向下推进
      this.messages.forEach(x => { x.locateMark = false; });
      m.decisionLabel = decisionLabel || "已完成决策";
      this.paused = false;
      this.refreshBranches();
    },
    // 阶段一：矛盾对选择
    sendPairChoice(choice) {
      if (!this.ws) return;
      const m = this.activePause();
      if (choice === "auto") {
        this.ws.send(JSON.stringify({ action: "auto" }));
        this.resolvePause("矛盾对交由系统自动选定");
      } else {
        this.ws.send(JSON.stringify({ action: "choose", index: choice }));
        const p = m && m.pairs[choice];
        this.resolvePause(p
          ? `已选定矛盾 ${choice + 1}：改善「${p.improving_param_name}」⇄ 恶化「${p.worsening_param_name}」`
          : `已选定矛盾 ${choice + 1}`);
      }
    },
    // 矛盾对编辑：展开参数目录下拉（改善/恶化可重选、交换方向、改具体描述）
    startPairEdit(m, j) {
      const p = m.pairs[j];
      m.editing = j;
      m.editForm = {
        imp: p.improving_param_id, wor: p.worsening_param_id,
        impText: p.improving_concrete || "", worText: p.worsening_concrete || "",
      };
    },
    startPairCustom(m) {
      m.editing = "custom";
      m.editForm = { imp: 15, wor: 1, impText: "", worText: "" };
    },
    cancelPairEdit(m) { m.editing = null; },
    swapPairForm(m) {
      const f = m.editForm;
      [f.imp, f.wor] = [f.wor, f.imp];
      [f.impText, f.worText] = [f.worText, f.impText];
    },
    sendPairEdited(m) {
      if (!this.ws) return;
      const f = m.editForm;
      const imp = Number(f.imp), wor = Number(f.wor);
      if (!imp || !wor || imp === wor) {
        alert("请从39个工程参数目录中选择两个不同的参数（改善 ≠ 恶化）。");
        return;
      }
      const payload = {
        action: "choose",
        pair: {
          improving_param_id: imp, worsening_param_id: wor,
          improving_concrete: (f.impText || "").trim(),
          worsening_concrete: (f.worText || "").trim(),
        },
      };
      if (m.editing !== "custom") payload.index = m.editing;
      this.ws.send(JSON.stringify(payload));
      const impName = (this.kn.parameter[imp] || {}).name || "";
      const worName = (this.kn.parameter[wor] || {}).name || "";
      this.resolvePause(m.editing === "custom"
        ? `自定义矛盾：改善「${impName}」⇄ 恶化「${worName}」`
        : `编辑矛盾 ${m.editing + 1}：改善「${impName}」⇄ 恶化「${worName}」`);
    },
    // 阶段三后：候选方案定夺（act: choose 基线 / accept 直接采纳 / auto）
    sendCandidateChoice(act) {
      if (!this.ws) return;
      const m = this.activePause();
      const idx = m ? m.chosen : 0;
      if (act === "auto") {
        this.ws.send(JSON.stringify({ action: "auto" }));
        this.resolvePause("方案定夺交由系统自动完成");
      } else if (act === "accept") {
        this.ws.send(JSON.stringify({ action: "accept", index: idx }));
        const c = m && m.candidates[idx];
        this.resolvePause(c ? `直接采纳方案 ${idx + 1}《${c.title}》为最终方案` : `已采纳方案 ${idx + 1}`);
      } else {
        this.ws.send(JSON.stringify({ action: "choose", index: idx }));
        const c = m && m.candidates[idx];
        this.resolvePause(c ? `选定方案 ${idx + 1}《${c.title}》作为优化基线` : `已选定方案 ${idx + 1}`);
      }
    },
    // 方案编辑：手动修改标题/正文后作为优化基线（后端会重新评分）
    startCandEdit(m, idx) {
      const full = this.candidates.find(c => c.index === idx)
        || (m.candidates || []).find(c => c.index === idx) || {};
      m.editing = idx;
      m.editForm = { title: full.title || "", text: full.text || "" };
    },
    cancelCandEdit(m) { m.editing = null; },
    sendCandEdited(m) {
      if (!this.ws) return;
      const f = m.editForm || {};
      if (!(f.text || "").trim()) { alert("方案内容不能为空。"); return; }
      this.ws.send(JSON.stringify({
        action: "choose_edit", index: m.editing,
        title: (f.title || "").trim(), text: f.text,
      }));
      this.resolvePause(`编辑方案 ${m.editing + 1}《${f.title || "修改版"}》后进入优化`);
    },
    // 阶段四：每轮优化后决策（act: continue / stop / feedback / auto）
    sendIterChoice(act) {
      if (!this.ws) return;
      const m = this.activePause();
      if (act === "auto") {
        this.ws.send(JSON.stringify({ action: "auto" }));
        this.resolvePause("后续优化轮次交由系统自动完成");
      } else if (act === "stop") {
        this.ws.send(JSON.stringify({ action: "stop" }));
        this.resolvePause("采纳当前方案，结束优化迭代");
      } else if (act === "feedback") {
        const hint = (m && m.hint || "").trim();
        this.ws.send(JSON.stringify({ action: "feedback", text: hint }));
        this.resolvePause(hint
          ? `按补充方向继续优化：「${hint.length > 24 ? hint.slice(0, 24) + "…" : hint}」`
          : "继续下一轮优化");
      } else {
        this.ws.send(JSON.stringify({ action: "continue" }));
        this.resolvePause("继续下一轮优化");
      }
    },
    // 优化轮次人工 override：强制采用本轮 / 保留原版 / 手动改稿后采用
    sendIterOverride(act) {
      if (!this.ws) return;
      const m = this.activePause();
      if (act === "adopt") {
        this.ws.send(JSON.stringify({ action: "adopt_round" }));
        this.resolvePause(`强制采用第 ${m ? m.iter : ""} 轮优化方案`);
      } else if (act === "discard") {
        this.ws.send(JSON.stringify({ action: "discard_round" }));
        this.resolvePause(`保留原方案，舍弃第 ${m ? m.iter : ""} 轮结果`);
      }
    },
    startIterEdit(m) {
      m.editing = true;
      m.editForm = { title: m.roundTitle || "", text: m.roundText || "" };
    },
    cancelIterEdit(m) { m.editing = false; },
    sendIterEdited(m) {
      if (!this.ws) return;
      const f = m.editForm || {};
      if (!(f.text || "").trim()) { alert("方案内容不能为空。"); return; }
      this.ws.send(JSON.stringify({
        action: "edit_round",
        title: (f.title || "").trim(), text: f.text,
      }));
      this.resolvePause(`手动修改第 ${m.iter} 轮方案并采用`);
    },
    toggleThink(m) {
      // 思考流式进行中不允许手动折叠（DeepSeek 行为：推演时始终锚定最新文本）
      if (m.thinkStreaming) return;
      m.thinkOpen = !m.thinkOpen;
    },
    scrollThinkLive() {
      // 锚定：思考灰字区始终滚动到最新生成的文本
      this.$nextTick(() => {
        const el = document.querySelector(".think-scroll.think-live");
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    handleEvent(ev) {
      switch (ev.type) {
        case "agent_start":
          this.activeAgent = { agent: ev.agent, title: ev.agent_title };
          break;
        case "thinking_start":
          this.activeAgent = null;
          if (this.streaming && this.streaming.agent !== ev.agent) this.streaming = null;
          if (!this.streaming) {
            this.streaming = {
              agent: ev.agent, agent_title: ev.agent_title,
              content: "", thinkContent: "", thinkStreaming: true, thinkOpen: true,
            };
          }
          break;
        case "thinking_delta": {
          if (!this.streaming || this.streaming.agent !== ev.agent) {
            this.streaming = {
              agent: ev.agent, agent_title: ev.agent_title,
              content: "", thinkContent: "", thinkStreaming: true, thinkOpen: true,
            };
          }
          this.streaming.thinkContent += ev.text;
          this.scrollThinkLive();
          break;
        }
        case "thinking_done":
          if (this.streaming && this.streaming.agent === ev.agent) {
            // 灰字收起，随后黑字正文在同一气泡内开始流出
            this.streaming.thinkStreaming = false;
            this.streaming.thinkOpen = false;
          }
          break;
        case "message_delta":
          if (this.streaming && this.streaming.agent !== ev.agent) {
            this.streaming = null;
          }
          this.activeAgent = null;
          if (!this.streaming) {
            this.streaming = {
              agent: ev.agent, agent_title: ev.agent_title,
              content: "", thinkContent: "", thinkStreaming: false, thinkOpen: false,
            };
          }
          this.streaming.content = ev.text;
          break;
        case "phase":
          this.phase = ev.index;
          this.activeAgent = null;
          this.streaming = null;
          this.messages.push({ type: "phase", ...ev });
          break;
        case "system":
          this.messages.push({ type: "system", content: ev.content });
          break;
        case "message": {
          if (this.activeAgent && this.activeAgent.agent === ev.agent) this.activeAgent = null;
          const st = (this.streaming && this.streaming.agent === ev.agent) ? this.streaming : null;
          if (st) this.streaming = null;
          this.messages.push({
            type: "msg", agent: ev.agent, agent_title: ev.agent_title,
            content: ev.content, refs: ev.refs || [],
            // 思考内容随气泡落盘（会话内可展开回看；刷新回放仅保留字数摘要）
            thinking: st && st.thinkContent ? st.thinkContent : null,
            thinkingChars: (st && st.thinkContent.length) || ev.thinking_chars || 0,
            thinkOpen: false,
          });
          break;
        }
        case "checkpoint":
          // 检查点仅用于分叉恢复，前端不展示
          break;
        case "pause": {
          this.activeAgent = null;
          this.streaming = null;
          this.paused = true;
          // 分叉复制的旧暂停点：渲染为历史记录，子分支会重新暂停等待决策
          if (ev.superseded) {
            this.messages.push({
              type: "pause", stage: ev.stage || "pair", done: true,
              superseded: true,
              decisionLabel: "分叉前的决策记录（新分支在此处重新决策）",
            });
            break;
          }
          // 人机交互节点作为瀑布流消息按序追加（位于所有既有内容下游）
          const stage = ev.stage || "pair";
          const m = {
            type: "pause", stage, done: false, superseded: false,
            decisionLabel: "", chosen: 0, editing: null, editForm: null,
            locateMark: false,
          };
          if (stage === "pair") {
            m.pairs = ev.pairs || [];
          } else if (stage === "candidate") {
            m.candidates = ev.candidates || [];
            m.chosen = ev.best_index || 0;
          } else if (stage === "iterate") {
            m.iter = ev.iteration;
            m.ideality = ev.ideality;
            m.bestIdeality = ev.best_ideality;
            m.improved = ev.improved;
            m.remaining = ev.remaining;
            m.roundTitle = ev.round_title || "";
            m.roundText = ev.round_text || "";
            m.hint = "";
          }
          this.messages.push(m);
          // 暂停点对应 checkpoint 已先落库，刷新分支轨道数据
          this.refreshBranches();
          // 分支切换定位：待定位目标存在时锚点出现即打标（节点命中后锁定、latest 跟随最新断点）；
          // 已锁定锚点不被后续暂停点清除——同阶段后续轮次（方案迭代多轮）让标记跟随最新暂停点
          if (this.pendingLocate != null) {
            this.checkPendingLocate();
          } else {
            const marked = this.messages.find(x =>
              x.locateMark && x.type === "pause" && x.stage === stage && x !== m);
            if (marked) {
              marked.locateMark = false;
              m.locateMark = true;
              this.scrollToLocate();
            }
          }
          break;
        }
        case "error":
          this.activeAgent = null;
          this.streaming = null;
          this.error = ev.message || "运行出错";
          this.running = false;
          this.refreshBranches();
          break;
        case "done":
          this.activeAgent = null;
          this.streaming = null;
          this.running = false;
          this.paused = false;
          // 历史回放：闭合所有未决策的交互节点（决策当时已发生，不可再交互）
          this.messages.forEach(m => {
            if (m.type === "pause" && !m.done) {
              m.done = true;
              m.decisionLabel = "（历史会话中已完成的决策）";
            }
          });
          // 方案完成节点锚点：对话流末尾补一条完成系统消息（实时结束/历史回放同一路径）
          this.messages.push({
            type: "system", kind: "final",
            content: "✅ 全流程完成，最终方案已生成（见右侧文档底部）",
          });
          if (this.pendingLocate === "latest") {
            // 历史回放完整：latest 定位释放，落到文档最底部的最终方案
            this.pendingLocate = null;
            this.messages.forEach(x => { x.locateMark = false; });
            if (this.final) this.scrollDocToFinal();
          } else {
            // 回放结束：待定位节点锚点已出现则居中；否则标记保留原位，无标记才滚到最终方案
            const located = this.checkPendingLocate();
            if (!located) {
              this.pendingLocate = null;
              if (this.messages.some(m => m.locateMark)) {
                this.scrollToLocate();   // 轨道节点定位优先于常规滚底
              } else if (this.final) {
                this.scrollDocToFinal();
              }
            }
          }
          this.refreshBranches();
          break;
        case "card":
          this.handleCard(ev);
          break;
      }
      this.$nextTick(() => {
        this.checkPendingLocate();
        // 定位锚点居中显示时，不被常规滚底覆盖
        if (this.messages.some(m => m.locateMark)) return;
        const chat = document.querySelector(".dialogue");
        if (chat) {
          const overflowY = window.getComputedStyle(chat).overflowY;
          if (overflowY === "auto" || overflowY === "scroll") {
            chat.scrollTop = chat.scrollHeight;
          }
        }
      });
    },
    handleCard(ev) {
      switch (ev.card) {
        case "params":
          this.params = { parameters: ev.parameters, summary: ev.summary };
          break;
        case "contradictions":
          this.pairs = ev.pairs || [];
          break;
        case "selected_pair":
          this.selectedPair = ev.pair;
          // 矛盾对交互节点随选定结果闭合（实时校正 / 回放恢复均走此路径）
          {
            const pm = this.activePause();
            if (pm && pm.stage === "pair") {
              const idx = (pm.pairs || []).findIndex(
                p => p.improving_param_id === ev.pair.improving_param_id
                  && p.worsening_param_id === ev.pair.worsening_param_id);
              if (idx >= 0) pm.chosen = idx;
              pm.done = true;
              pm.decisionLabel = `已选定矛盾 ${(idx >= 0 ? idx : pm.chosen) + 1}`;
              this.paused = false;
            }
          }
          break;
        case "principles":
          this.principles = ev.principles;
          this.principleSource = ev.source;
          break;
        case "candidate": {
          const idx = this.candidates.findIndex(c => c.index === ev.index);
          const cand = {
            index: ev.index, title: ev.title, text: ev.text,
            principle_id: ev.principle_id, principle_name: ev.principle_name,
            expanded: ev.expanded, hull_volume: ev.hull_volume,
          };
          if (idx >= 0) this.candidates[idx] = cand; else this.candidates.push(cand);
          this.hullSeries = ev.volume_series || this.hullSeries;
          this.hullRatio = ev.volume_ratio || this.hullRatio;
          break;
        }
        case "hull_summary":
          this.hullSeries = ev.series || this.hullSeries;
          this.hullRatio = ev.ratio || this.hullRatio;
          break;
        case "score_item": {
          const item = {
            index: ev.index, title: ev.title, benefit: ev.benefit,
            cost: ev.cost, harm: ev.harm, ideality: ev.ideality,
          };
          const idx = this.scores.findIndex(s => s.index === item.index);
          if (idx >= 0) this.scores[idx] = item; else this.scores.push(item);
          break;
        }
        case "scores":
          this.bestIndex = ev.best_index;
          break;
        case "optimization":
          this.optimizations.unshift({
            iteration: ev.iteration, text: ev.text, title: ev.title,
            ideality: ev.ideality, improved: ev.improved,
            series: ev.series || [],
          });
          break;
        case "final":
          this.final = ev;
          this.scrollDocToFinal();
          break;
      }
    },
  },
}).mount("#app");
