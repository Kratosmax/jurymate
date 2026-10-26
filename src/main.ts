import { createApp } from "vue";
import "./style.css";
import App from "./App.vue";

const app = createApp(App);

// 单个组件渲染异常不应让整窗变白：捕获后写入控制台并展示可读提示，
// 而不是留下一个只有侧栏的空界面。
app.config.errorHandler = (err, _instance, info) => {
  console.error("[jurymate] 渲染异常:", err, info);
  const host = document.querySelector(".view-host");
  if (host && !host.querySelector(".fatal-error")) {
    const box = document.createElement("div");
    box.className = "hintbar fatal-error";
    box.style.margin = "22px 26px";
    box.innerHTML =
      '<b style="color:var(--bad)">界面渲染出错</b>' +
      '<div style="margin-top:6px;font-size:12px;color:var(--dim)">' +
      String(err) +
      "</div><div style=\"margin-top:8px;font-size:12px\">" +
      "可尝试重启程序；若持续出现，请到项目 Issues 反馈上面的信息。</div>";
    host.prepend(box);
  }
};

app.mount("#app");
