# VideoScope

Aplicativo local para localizar vídeos em uma página ou varrer links de um domínio, listar os resultados e baixar vídeo ou áudio MP3.

## Iniciar no Windows

Abra `iniciar.bat`. A primeira execução baixa Python e instala dependências na pasta `.runtime`, incluindo FFmpeg e Chromium para analisar JavaScript. Depois, acesse **http://127.0.0.1:8765**. A instalação requer internet; nenhuma instalação global é necessária. Use Ctrl+C na janela do serviço para encerrar.

1. Informe a URL inicial.
2. Opcionalmente, informe o domínio permitido. A URL inicial precisa pertencer a ele. Subdomínios são incluídos.
3. Escolha somente a página ou percorrer o domínio. Configure até 200 páginas e profundidade 5. A opção de analisar JavaScript vem ativada e carrega páginas dinâmicas quando a análise inicial não encontra mídia.
4. Inicie a varredura. Os resultados aparecem durante a análise.
5. Escolha **Baixar vídeo** ou **Áudio MP3**. Quando terminar, clique em **Salvar arquivo**.

O domínio limita as páginas visitadas; vídeos dessas páginas podem estar hospedados em CDNs ou provedores externos. A busca identifica tags HTML de vídeo, fontes, iframes reconhecidos pelo extrator, metadados e links de mídia em HTML/scripts. Players **VTurb/ConverteAI** são detectados lendo a configuração pública de `player.js`, inclusive scripts adiados pelo LiteSpeed, `/players/.../v4/player.js` e `/ab-test/.../player.js`. A busca usa o identificador real da mídia, valida o manifesto HLS e seleciona a variante realmente exibida em testes A/B. Configurações marcadas como DRM não são extraídas. O yt-dlp resolve outros players compatíveis. A varredura é limitada e não garante descobrir todos os vídeos de um site: páginas sem links acessíveis, outros players que dependem de execução de JavaScript, login, CAPTCHA, URLs expiradas e DRM podem impedir a detecção ou o download. Não há quebra de DRM; quizzes e autenticações precisam ser concluídos pelo usuário na janela de análise.

### Páginas dinâmicas e vídeos escolhidos por quiz

A detecção VTurb não depende do domínio do site. Além do HTML, o aplicativo usa um Chromium local para acompanhar scripts e manifestos realmente carregados pela página. Se o site escolher o vídeo após respostas de quiz ou depender da sua sessão, abra **Vídeos dinâmicos, quiz e código VTurb**, marque **Abrir página e aguardar o vídeo** e inicie a varredura. Responda ao quiz na janela que abrir até o vídeo aparecer. A análise aguarda até 10 minutos e a sessão desse navegador é preservada em `.runtime/browser-profile`; ela é separada da sessão do Chrome/Edge pessoal. Há apenas um navegador de análise por vez. Uma varredura automática aguarda até 25 segundos por página dinâmica.

Outra opção é colar o código da tag `vturb-smartplayer` junto com o script, ou a URL completa de `player.js`, no campo **Código do player ou URL do player.js**, mantendo a URL da página como origem. Apenas o ID da tag não informa a conta/provedor da mídia e pode ser insuficiente. O código informado é lido como texto; não é executado. URLs de players escapadas em JSON e sem protocolo explícito também são reconhecidas.

Sem as respostas ou o código do player carregado, uma URL de quiz pode mostrar apenas a tela inicial: o aplicativo não inventa respostas nem escolhe outro vídeo. Scripts de player presentes em respostas de API, mas não carregados na página, não são incluídos pela análise dinâmica. A navegação do navegador fica limitada ao domínio da varredura, com recursos externos públicos permitidos. O perfil de análise e os downloads podem ser apagados manualmente com o serviço encerrado.

Os vídeos diretos aparecem como candidatos; a disponibilidade real é confirmada no download. Vídeos sem faixa de áudio não podem ser convertidos em MP3. A opção vídeo conserva o melhor formato disponível e tenta mesclar em MP4; a extensão final depende da fonte. Áudio é convertido em MP3 a 192 kbps.

Arquivos permanecem em `downloads/`, em subpastas por tarefa. Estados e resultados ficam na memória e são apagados quando o serviço reinicia; arquivos já gerados permanecem no disco. Apague arquivos antigos manualmente para liberar espaço. Existem limites de duas varreduras e três tarefas de download simultâneas. O botão Parar encerra a varredura após a análise atual.

O serviço foi feito para uso pessoal, ligado apenas a `127.0.0.1`. Não o exponha à internet. URLs iniciais e redirecionamentos da varredura recusam destinos privados; requisições internas de provedores feitas pelo yt-dlp seguem o comportamento desse extrator. Baixe apenas mídia que você tenha autorização para salvar.

## Desenvolvimento

Com Python 3.11 ou superior: `pip install -r requirements.txt`, defina `PLAYWRIGHT_BROWSERS_PATH` como o caminho absoluto de `.runtime/browsers`, execute `python -m playwright install chromium` e depois `python -m uvicorn app:app --host 127.0.0.1 --port 8765`.

Testes: `.runtime/python/python.exe -m unittest discover -s tests -v`.

Teste completo opcional, com o serviço ligado e internet: `.runtime/python/python.exe tests/smoke_live.py`. Usa um vídeo CC0 do MDN e verifica download de vídeo e conversão MP3. Os arquivos de exemplo ficam em `downloads/`.

Para verificar os dois formatos VTurb e baixar vídeo/áudio da página de reprodução usada na correção: `.runtime/python/python.exe tests/smoke_vturb_live.py`. Esse teste depende da disponibilidade externa da página e dos players e mantém os arquivos baixados em `downloads/`.

Referências: [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FastAPI](https://fastapi.tiangolo.com/).

Teste da renderização dinâmica, usando páginas simuladas em dois domínios sem acessar sites externos: `.runtime/python/python.exe tests/smoke_dynamic.py`. Requer o Chromium preparado pelo inicializador.

No modo interativo a análise acompanha mudanças de URL dentro do domínio e novas abas, lê os frames e entrega resultados assim que consegue resolver a mídia. A opção VTurb `secure` indica entrega de mídia com URL de sessão e não é tratada como DRM: o aplicativo usa o manifesto realmente carregado pelo player, sem inventar tokens ou URLs. Conteúdos marcados com `video.drm` continuam sem extração. O progresso informa a página atual, os players e os manifestos recebidos.

A análise com JavaScript seleciona apenas players VTurb visíveis, incluindo os que ficam em frames ou componentes com shadow DOM. Os segmentos precisam corresponder ao ID de mídia do player, e a duração é comparada com a reprodução quando disponível. Manifestos e segmentos que apontem para um vídeo substituto são descartados. O download usa uma cópia local do manifesto de segmentos recebido pelo navegador, com URLs absolutas, para que uma nova consulta ao servidor não troque a mídia. Qualidades recebidas do mesmo vídeo são reunidas em um resultado. A interface marca esses resultados como **Reprodução visível validada**. Se a análise não recebeu os segmentos, inicie a reprodução na janela de análise.
