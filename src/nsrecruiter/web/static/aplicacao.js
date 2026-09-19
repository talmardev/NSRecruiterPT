/* NSRecruiter, dashboard web. Poll simples a /api/fotografia, sem framework. */
(function () {
  "use strict";

  var INTERVALO_ATUALIZACAO_MS = 1000;
  var CHAVE_TEMA_ARMAZENADO = "nsrecruiter-web-tema";

  var ROTULOS_ESTADO = {
    sending: "A ENVIAR",
    waiting: "EM ESPERA",
    paused: "PAUSADO",
    blocked: "BLOQUEADO",
    rate_limited: "LIMITADO",
  };

  var ORDEM_FUNIL = ["discovered", "queued", "validating", "sending", "sent", "failed", "rejected"];
  var ROTULOS_FUNIL = {
    discovered: "Descobertos",
    queued: "Na fila",
    validating: "A validar",
    sending: "A enviar",
    sent: "Enviados",
    failed: "Falhados",
    rejected: "Rejeitados",
  };

  var ROTULOS_CATEGORIA = {
    direta: "Direta (API)",
    heuristica: "Deteccao de padroes",
    expirado: "Expirados (fila)",
  };

  var graficos = {};
  var intervaloAtual = "hourly";
  var aPedir = false;

  function variavelCss(nome) {
    return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
  }

  // ---------- tema ----------

  function aplicarTema(tema) {
    if (tema === "dark" || tema === "light") {
      document.documentElement.setAttribute("data-theme", tema);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    var icone = document.getElementById("theme-toggle-icon");
    var escuro = tema === "dark" || (tema !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    icone.textContent = escuro ? "☀️" : "🌙";
    atualizarTemaGraficos();
  }

  function iniciarTema() {
    var guardado = null;
    try {
      guardado = localStorage.getItem(CHAVE_TEMA_ARMAZENADO);
    } catch (erro) {
      /* localStorage indisponivel (ex: modo privado). Fica no tema do sistema */
    }
    aplicarTema(guardado);
    document.getElementById("theme-toggle").addEventListener("click", function () {
      var atual = document.documentElement.getAttribute("data-theme");
      var escuro = atual === "dark" || (!atual && window.matchMedia("(prefers-color-scheme: dark)").matches);
      var proximo = escuro ? "light" : "dark";
      try {
        localStorage.setItem(CHAVE_TEMA_ARMAZENADO, proximo);
      } catch (erro) {
        /* ignora, so afeta persistencia entre visitas */
      }
      aplicarTema(proximo);
    });
  }

  // ---------- formatacao ----------

  function formatarMMSS(segundos) {
    var total = Math.max(0, Math.floor(segundos || 0));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function formatarHHMMSS(segundos) {
    var total = Math.max(0, Math.floor(segundos || 0));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function classeBarraLimite(restante, limite) {
    if (restante === null || restante === undefined || !limite) return "bar-track-empty";
    var fracao = restante / limite;
    if (fracao > 0.5) return "bar-green";
    if (fracao > 0.2) return "bar-yellow";
    return "bar-red";
  }

  function rotuloHora(intervaloIso) {
    var data = new Date(intervaloIso);
    return data.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function rotuloDia(intervaloIso) {
    var data = new Date(intervaloIso + "T00:00:00Z");
    return data.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" });
  }

  // ---------- render: topo / estado ao vivo ----------

  function desenharInfo(fotografia) {
    var info = fotografia.info || {};
    document.getElementById("meta-region").textContent = "Regiao: " + (info.regiao || "--");
    document.getElementById("meta-nation").textContent = "Nacao: " + (info.nacao || "--");

    var aoVivo = fotografia.ao_vivo || {};
    var etiquetaDryRun = document.getElementById("dry-run-badge");
    etiquetaDryRun.hidden = !aoVivo.modo_simulacao;

    var etiqueta = document.getElementById("live-badge");
    var textoEtiqueta = document.getElementById("live-badge-text");
    etiqueta.classList.remove("live-online", "live-offline", "live-unknown");
    if (aoVivo.ligado) {
      etiqueta.classList.add("live-online");
      textoEtiqueta.textContent = "processo ligado";
    } else {
      etiqueta.classList.add("live-offline");
      textoEtiqueta.textContent = "processo desligado";
    }

    document.getElementById("offline-banner").hidden = !!aoVivo.ligado || fotografia.tem_dados === false;
    document.getElementById("no-data-banner").hidden = fotografia.tem_dados !== false;

    var gerado = new Date(fotografia.gerado_em);
    document.getElementById("generated-at").textContent = "Atualizado as " + gerado.toLocaleTimeString();
  }

  function desenharPainelAoVivo(fotografia) {
    var aoVivo = fotografia.ao_vivo || {};
    var pilula = document.getElementById("app-state-pill");
    pilula.className = "state-pill";

    if (aoVivo.ligado && aoVivo.estado_app) {
      pilula.classList.add("state-" + aoVivo.estado_app);
      pilula.textContent = ROTULOS_ESTADO[aoVivo.estado_app] || aoVivo.estado_app.toUpperCase();
      document.getElementById("uptime").textContent = "Uptime: " + formatarHHMMSS(aoVivo.segundos_atividade);
    } else {
      pilula.classList.add("state-unknown");
      pilula.textContent = "DESCONHECIDO";
      document.getElementById("uptime").textContent = "Uptime: --:--:--";
    }

    var intervalo = aoVivo.intervalo_envio_segundos || 182;
    var ateProximo = aoVivo.ligado ? aoVivo.segundos_ate_proximo_envio || 0 : intervalo;
    var fracaoCooldown = aoVivo.ligado ? Math.min(1, Math.max(0, (intervalo - ateProximo) / intervalo)) : 0;
    document.getElementById("next-send-label").textContent = aoVivo.ligado ? formatarMMSS(ateProximo) : "--:--";
    document.getElementById("cooldown-bar").style.width = fracaoCooldown * 100 + "%";

    var restante = aoVivo.ligado ? aoVivo.restante_geral : null;
    var limite = aoVivo.ligado ? aoVivo.limite_geral : null;
    var barraGeral = document.getElementById("general-bar");
    barraGeral.className =
      "bar-fill " + (aoVivo.ligado ? classeBarraLimite(restante, limite).replace("bar-track-empty", "bar-green") : "bar-green");
    var fracao = restante !== null && restante !== undefined && limite ? restante / limite : 0;
    barraGeral.style.width = (aoVivo.ligado ? fracao * 100 : 0) + "%";
    document.getElementById("general-limit-label").textContent =
      aoVivo.ligado && restante !== null && restante !== undefined ? restante + "/" + limite : "sem dados";

    var linhaRetry = document.getElementById("retry-line");
    if (aoVivo.ligado && aoVivo.motivo_bloqueio) {
      linhaRetry.textContent =
        "Retry-After ativo: EM ESPERA (" + aoVivo.motivo_bloqueio + "): " + formatarMMSS(aoVivo.segundos_bloqueio);
      linhaRetry.classList.add("is-active");
    } else {
      linhaRetry.textContent = aoVivo.ligado
        ? "Retry-After ativo: nenhuma"
        : "Retry-After ativo: indisponivel (processo desligado)";
      linhaRetry.classList.remove("is-active");
    }
  }

  function desenharEstatisticas(fotografia) {
    var contagens = fotografia.contagens;
    if (!contagens) return;
    document.getElementById("stat-sent").textContent = contagens.enviados_hoje + " / " + contagens.enviados_total;
    document.getElementById("stat-success-rate").textContent =
      contagens.taxa_sucesso === null || contagens.taxa_sucesso === undefined
        ? "sem dados"
        : Math.round(contagens.taxa_sucesso * 100) + "%";
    document.getElementById("stat-queue").textContent = contagens.tamanho_fila;
    document.getElementById("stat-rejected-direct").textContent = contagens.rejeitados_diretos_total;
    document.getElementById("stat-rejected-pattern").textContent = contagens.rejeitados_padrao_total;
    document.getElementById("stat-expired").textContent = contagens.expirados_total;
    document.getElementById("stat-rate-hour").textContent = contagens.enviados_ultima_hora + "/h";
    document.getElementById("stat-projection").textContent = "~" + contagens.projecao_24h;
  }

  // ---------- graficos ----------

  function paletaGraficos() {
    return {
      texto: variavelCss("--text-dim"),
      grelha: variavelCss("--border"),
      verde: variavelCss("--green"),
      vermelho: variavelCss("--red"),
      destaque: variavelCss("--accent"),
      ciano: variavelCss("--cyan"),
      amarelo: variavelCss("--yellow"),
      laranja: variavelCss("--orange"),
      fonteMono: variavelCss("--fonte-mono"),
    };
  }

  function opcoesBase(extra) {
    var paleta = paletaGraficos();
    var opcoes = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { labels: { color: paleta.texto, boxWidth: 12, font: { size: 11, family: paleta.fonteMono } } },
        tooltip: { titleFont: { size: 11, family: paleta.fonteMono }, bodyFont: { size: 11, family: paleta.fonteMono } },
      },
      scales: {},
    };
    return Object.assign(opcoes, extra || {});
  }

  function iniciarGraficos() {
    var paleta = paletaGraficos();

    graficos.serieTemporal = new Chart(document.getElementById("chart-timeseries"), {
      data: {
        labels: [],
        datasets: [
          {
            type: "bar",
            label: "Enviados",
            data: [],
            backgroundColor: paleta.verde,
            stack: "tentativas",
            borderRadius: 3,
            order: 2,
          },
          {
            type: "bar",
            label: "Falhados",
            data: [],
            backgroundColor: paleta.vermelho,
            stack: "tentativas",
            borderRadius: 3,
            order: 2,
          },
          {
            type: "line",
            label: "Descobertos",
            data: [],
            borderColor: paleta.destaque,
            backgroundColor: "transparent",
            tension: 0.3,
            pointRadius: 0,
            yAxisID: "y1",
            order: 1,
          },
        ],
      },
      options: opcoesBase({
        scales: {
          x: { ticks: { color: paleta.texto, maxRotation: 0, autoSkip: true, font: { size: 10, family: paleta.fonteMono } }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: paleta.texto, precision: 0, font: { family: paleta.fonteMono } }, grid: { color: paleta.grelha } },
          y1: { beginAtZero: true, position: "right", ticks: { color: paleta.texto, precision: 0, font: { family: paleta.fonteMono } }, grid: { display: false } },
        },
      }),
    });

    graficos.funil = new Chart(document.getElementById("chart-funnel"), {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Alvos", data: [], backgroundColor: paleta.destaque, borderRadius: 3 }] },
      options: opcoesBase({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { color: paleta.texto, precision: 0, font: { family: paleta.fonteMono } }, grid: { color: paleta.grelha } },
          y: { ticks: { color: paleta.texto, font: { size: 11, family: paleta.fonteMono } }, grid: { display: false } },
        },
      }),
    });

    graficos.categoriaRejeicao = new Chart(document.getElementById("chart-rejection-category"), {
      type: "doughnut",
      data: { labels: [], datasets: [{ data: [], backgroundColor: [paleta.ciano, paleta.laranja, paleta.amarelo] }] },
      options: opcoesBase({
        plugins: { legend: { position: "bottom", labels: { color: paleta.texto, boxWidth: 12, font: { size: 11 } } } },
      }),
    });

    graficos.motivosRejeicao = new Chart(document.getElementById("chart-rejection-reasons"), {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Rejeicoes", data: [], backgroundColor: paleta.laranja, borderRadius: 3 }] },
      options: opcoesBase({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { color: paleta.texto, precision: 0, font: { family: paleta.fonteMono } }, grid: { color: paleta.grelha } },
          y: {
            afterFit: function (escala) { escala.width = Math.max(escala.width, 380); },
            ticks: { color: paleta.texto, font: { size: 8, family: paleta.fonteMono } },
            grid: { display: false },
          },
        },
      }),
    });

    graficos.motivosFalha = new Chart(document.getElementById("chart-failure-reasons"), {
      type: "bar",
      data: { labels: [], datasets: [{ label: "Falhas", data: [], backgroundColor: paleta.vermelho, borderRadius: 3 }] },
      options: opcoesBase({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { color: paleta.texto, precision: 0, font: { family: paleta.fonteMono } }, grid: { color: paleta.grelha } },
          y: { ticks: { color: paleta.texto, font: { size: 9, family: paleta.fonteMono } }, grid: { display: false } },
        },
      }),
    });
  }

  function atualizarTemaGraficos() {
    if (!graficos.serieTemporal) return;
    var paleta = paletaGraficos();
    Object.keys(graficos).forEach(function (chave) {
      var grafico = graficos[chave];
      grafico.options.plugins.legend.labels.color = paleta.texto;
      if (grafico.options.scales.x) grafico.options.scales.x.ticks.color = paleta.texto;
      if (grafico.options.scales.y) grafico.options.scales.y.ticks.color = paleta.texto;
      if (grafico.options.scales.y1) grafico.options.scales.y1.ticks.color = paleta.texto;
      if (grafico.options.scales.x && grafico.options.scales.x.grid) grafico.options.scales.x.grid.color = paleta.grelha;
      if (grafico.options.scales.y && grafico.options.scales.y.grid) grafico.options.scales.y.grid.color = paleta.grelha;
      grafico.update("none");
    });
  }

  var ultimaFotografia = null;

  function desenharSerieTemporal(fotografia) {
    var serie;
    if (intervaloAtual === "hourly") serie = fotografia.serie_horaria;
    else if (intervaloAtual === "daily") serie = fotografia.serie_diaria;
    else serie = fotografia.serie_completa;
    serie = serie || [];
    var funcaoRotulo = intervaloAtual === "hourly" ? rotuloHora : rotuloDia;
    graficos.serieTemporal.data.labels = serie.map(function (ponto) { return funcaoRotulo(ponto.intervalo); });
    graficos.serieTemporal.data.datasets[0].data = serie.map(function (ponto) { return ponto.enviados; });
    graficos.serieTemporal.data.datasets[1].data = serie.map(function (ponto) { return ponto.falhados; });
    graficos.serieTemporal.data.datasets[2].data = serie.map(function (ponto) { return ponto.descobertos; });
    graficos.serieTemporal.update();
  }

  function desenharFunil(fotografia) {
    var funil = fotografia.funil || {};
    graficos.funil.data.labels = ORDEM_FUNIL.map(function (chave) { return ROTULOS_FUNIL[chave]; });
    graficos.funil.data.datasets[0].data = ORDEM_FUNIL.map(function (chave) { return funil[chave] || 0; });
    graficos.funil.update();
  }

  function desenharCategoriaRejeicao(fotografia) {
    var linhas = fotografia.categorias_rejeicao || [];
    var ordem = ["direta", "heuristica", "expirado"];
    var porCategoria = {};
    linhas.forEach(function (linha) { porCategoria[linha.categoria] = linha.quantidade; });
    graficos.categoriaRejeicao.data.labels = ordem.map(function (chave) { return ROTULOS_CATEGORIA[chave]; });
    graficos.categoriaRejeicao.data.datasets[0].data = ordem.map(function (chave) { return porCategoria[chave] || 0; });
    graficos.categoriaRejeicao.update();
  }

  function desenharGraficoMotivos(grafico, linhas) {
    linhas = linhas || [];
    grafico.data.labels = linhas.map(function (linha) {
      return linha.motivo.length > 90 ? linha.motivo.slice(0, 87) + "..." : linha.motivo;
    });
    grafico.data.datasets[0].data = linhas.map(function (linha) { return linha.quantidade; });
    grafico.update();
  }

  // ---------- ciclo principal ----------

  function desenhar(fotografia) {
    ultimaFotografia = fotografia;
    desenharInfo(fotografia);
    desenharPainelAoVivo(fotografia);
    if (fotografia.tem_dados) {
      desenharEstatisticas(fotografia);
      desenharSerieTemporal(fotografia);
      desenharFunil(fotografia);
      desenharCategoriaRejeicao(fotografia);
      desenharGraficoMotivos(graficos.motivosRejeicao, fotografia.motivos_rejeicao);
      desenharGraficoMotivos(graficos.motivosFalha, fotografia.motivos_falha);
    }
  }

  function obterFotografia() {
    if (aPedir) return;
    aPedir = true;
    fetch("/api/fotografia", { cache: "no-store" })
      .then(function (resposta) { return resposta.json(); })
      .then(function (fotografia) { desenhar(fotografia); })
      .catch(function (erro) {
        console.error("Falha a obter /api/fotografia:", erro);
      })
      .finally(function () {
        aPedir = false;
      });
  }

  function iniciarAlternadorIntervalo() {
    var botoes = document.querySelectorAll("#timeseries-toggle .segmented-btn");
    botoes.forEach(function (botao) {
      botao.addEventListener("click", function () {
        botoes.forEach(function (b) { b.classList.remove("is-active"); });
        botao.classList.add("is-active");
        intervaloAtual = botao.getAttribute("data-range");
        if (ultimaFotografia && ultimaFotografia.tem_dados) desenharSerieTemporal(ultimaFotografia);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    iniciarTema();
    iniciarGraficos();
    iniciarAlternadorIntervalo();
    obterFotografia();
    setInterval(obterFotografia, INTERVALO_ATUALIZACAO_MS);
  });
})();
