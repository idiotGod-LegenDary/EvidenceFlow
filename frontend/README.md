# EvidenceFlow 工作台

React、TypeScript 和 Vite 实现的售后工单界面，包含工单列表、客户会话、订单事实、政策证据、确认操作和处理时间线。

## 开发

使用 Node.js 22.12+。先在项目根目录启动 FastAPI，开发端口默认为 8002，再运行：

```bash
cd frontend
npm ci
npm run dev
```

页面地址为 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到 `http://127.0.0.1:8002`；独立部署可通过 `VITE_API_BASE` 指定后端地址。

## 检查与构建

```bash
npm test
npm run build
```

构建输出为 `frontend/dist`，FastAPI 可在 `/` 提供该目录中的页面。

## 数据与操作

工单列表、会话记录、证据和执行结果从后端读取。待审核动作保存在数据库，服务重启后可继续查看和确认。对话通过 SSE 接收完成校验的回答与引用；确认和撤销通过工单接口执行。

`VITE_DEMO_USER_ID` 默认为 `u_demo`，用于本地演示。当前后端尚未接入可信用户认证，生产部署需要由服务端绑定身份。纯咨询和未形成提议的对话不显示为独立工单。
