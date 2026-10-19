// 업로드 페이지 진입점
import { CONFIG } from "./settings.js";
import { initUpload } from "./upload.js";
import { showModeBadge } from "./ui.js";

showModeBadge(CONFIG);
initUpload();
