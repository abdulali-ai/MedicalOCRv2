/**
 * PM2 process file for host-side vLLM (Desktop vllm_env).
 *
 * Start:  pm2 start /home/utec/ocr/deploy/vllm/ecosystem.config.cjs
 * Stop:   pm2 stop vllm
 * Logs:   pm2 logs vllm
 */
module.exports = {
  apps: [
    {
      name: "vllm",
      script: "/home/utec/ocr/deploy/vllm/run-vllm.sh",
      interpreter: "bash",
      cwd: "/home/utec/ocr",
      autorestart: true,
      max_restarts: 10,
      min_uptime: "30s",
      kill_timeout: 30000,
      env: {
        OCR_REPO: "/home/utec/ocr",
        VLLM_VENV: "/home/utec/Desktop/vllm_env",
        VLLM_MODEL_DIR: "/home/utec/ocr/models/NuExtract3-FP8",
        VLLM_HOST: "0.0.0.0",
        VLLM_PORT: "8000",
        VLLM_MAX_MODEL_LEN: "32768",
        HF_HOME: "/home/utec/.cache/huggingface",
        HF_HUB_OFFLINE: "1",
      },
    },
  ],
};
