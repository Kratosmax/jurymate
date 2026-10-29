; JuryMate 安装钩子
;
; 0.3.3 起 sidecar 从 PyInstaller --onefile 改成 --onedir：
;   旧：dist-backend\jury-backend.exe          （单文件 468MB）
;   新：dist-backend\jury-backend\jury-backend.exe（目录 1.26GB）
; NSIS 升级时只会覆盖同名文件，不会删掉新版里已不存在的旧文件。
; 不清理的话用户盘上会一直多留 468MB，而且分不清哪个是正在用的后端。
;
; 0.3.3 起评分改 ONNX，sidecar 里不再带 torch/torchvision/pyiqa/pandas 等；
; 升级时同样必须整目录删掉旧版依赖，否则新包只有 424MB，旧目录却会留下 1.2GB。

!macro NSIS_HOOK_PREINSTALL
  ; UI 包不再携带 sidecar；旧版 full 包升级时整个 dist-backend 都该清掉
  RMDir /r "$INSTDIR\dist-backend"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  Delete "$INSTDIR\dist-backend\jury-backend.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  RMDir /r "$INSTDIR\dist-backend\jury-backend"
!macroend
