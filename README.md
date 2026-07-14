# Agente de Busca de Vagas — Herrysson Lira

Busca vagas em APIs gratuitas (Arbeitnow, RemoteOK, Adzuna, Jooble), calcula
um score de compatibilidade com seu currículo, estima a faixa salarial e
grava tudo numa planilha Google Sheets. Roda sozinho via GitHub Actions,
de graça, a cada 3 horas.

## 1. Criar o repositório

1. Crie um repositório **público** no GitHub (repos públicos têm minutos de
   Actions ilimitados no plano free; privados têm 2.000 min/mês, que também
   dá de sobra rodando a cada 3h, mas público é mais seguro).
2. Suba estes arquivos mantendo a estrutura de pastas (o `.github/workflows`
   precisa ficar exatamente nesse caminho).

## 2. Criar a planilha Google Sheets

1. Crie uma planilha nova no Google Sheets (pode deixar em branco, o script
   cria as abas "Vagas" e "IDs_Processados" sozinho).
2. Copie o **ID da planilha** (a string longa na URL, entre `/d/` e `/edit`).

## 3. Criar a Service Account do Google (gratuito)

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/), crie
   um projeto novo (ou use um existente).
2. Ative a **Google Sheets API** (menu "APIs e Serviços" → "Ativar APIs").
3. Vá em "Credenciais" → "Criar credenciais" → "Conta de serviço".
4. Após criar, entre na conta de serviço → aba "Chaves" → "Adicionar chave"
   → JSON. Isso baixa um arquivo `.json` — guarde o conteúdo inteiro dele.
5. Copie o e-mail da conta de serviço (algo como
   `nome@projeto.iam.gserviceaccount.com`) e **compartilhe sua planilha**
   com esse e-mail, dando permissão de Editor.

## 4. Criar as chaves de API gratuitas

- **Adzuna**: cadastro grátis em https://developer.adzuna.com/ → gera
  `app_id` e `app_key` (free tier: 250 chamadas/mês).
- **Jooble**: cadastro grátis em https://jooble.org/api/about → gera uma
  chave de API.
- Arbeitnow e RemoteOK não precisam de chave.

## 5. (Opcional) Criar bot do Telegram para alertas

Só faça isso se quiser receber uma mensagem quando aparecer uma vaga com
IO ≥ 90.

1. No Telegram, converse com o **@BotFather** → `/newbot` → escolha um nome.
   Ele te dá um **token** (guarde).
2. Envie qualquer mensagem pro seu bot recém-criado (senão ele não consegue
   te responder depois).
3. Acesse `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador
   e procure o campo `"chat":{"id": ...}` — esse número é seu `chat_id`.

## 6. (Opcional) Google Places API para reputação da empresa

Usa a mesma conta do Google Cloud do passo 3.

1. No mesmo projeto do Google Cloud Console, ative a **Places API**.
2. Em "Credenciais" → "Criar credenciais" → "Chave de API".
3. Restrinja essa chave só pra Places API (mais seguro).
4. Free tier: US$200 de crédito mensal, mais que suficiente pro volume
   deste projeto (cada busca de empresa custa frações de centavo).

Se você pular os passos 5 e 6, o agente funciona normalmente — só não
manda alertas e usa uma nota neutra (60%) pra reputação de empresa.

## 7. Configurar os Secrets no GitHub

No repositório: Settings → Secrets and variables → Actions → New repository
secret. Crie os 5 obrigatórios + os 3 opcionais se configurou os passos acima:

| Nome | Valor | Obrigatório? |
|---|---|---|
| `ADZUNA_APP_ID` | app_id do Adzuna | Sim |
| `ADZUNA_APP_KEY` | app_key do Adzuna | Sim |
| `JOOBLE_API_KEY` | chave do Jooble | Sim |
| `GOOGLE_SHEET_ID` | ID da planilha (passo 2) | Sim |
| `GOOGLE_CREDS_JSON` | JSON completo da service account (passo 3) | Sim |
| `TELEGRAM_BOT_TOKEN` | token do BotFather | Não |
| `TELEGRAM_CHAT_ID` | seu chat_id | Não |
| `GOOGLE_PLACES_API_KEY` | chave da Places API | Não |

## 8. Rodar

- O workflow já roda sozinho a cada 3h (`cron: "0 */3 * * *"`).
- Pra testar imediatamente sem esperar: aba "Actions" no GitHub → selecione
  "Job Search Agent" → "Run workflow".

## Como funciona o Índice de Oportunidade (IO) e o salário

- **IO (0-100)**, com pesos configuráveis em `resume_profile.json`:
  compatibilidade técnica (35%), modalidade (25%), salário dentro da faixa
  alvo (20%), reputação da empresa via Google (10%), senioridade compatível
  (5%) e benefícios citados na vaga (5%).
- A planilha mostra o breakdown de cada critério em colunas separadas, mais
  uma coluna **"Motivo"** com um resumo em texto do porquê daquela nota
  (ex: "Forte aderência técnica, remoto, salário dentro da faixa alvo").
- Vagas com IO ≥ 90 disparam um alerta no Telegram (se configurado).
- Coluna **"Status"**: toda vaga nova entra como "Nova" — você atualiza
  manualmente pra "Aplicada", "Entrevista", "Recusada" etc. conforme avança.
- **Salário**: se a vaga informa o valor, ele é usado (marcado "Informado").
  Se não informa, o script estima com base em senioridade do título + stack
  técnica + localização, usando uma tabela de benchmark de mercado (marcado
  "Estimado"). Isso é 100% baseado em regras, sem custo de API.
- A planilha é ordenada com **home office primeiro, depois híbrido, depois
  presencial**, e dentro de cada grupo por score decrescente.
- Vagas já vistas não se repetem (controle na aba "IDs_Processados").

## Limitações a saber

- Não inclui LinkedIn, Indeed, Gupy, InfoJobs ou Catho: essas plataformas
  não oferecem API pública, e fazer scraping nelas viola os Termos de Uso
  e derruba o IP das Actions. Ficamos só com fontes legítimas e gratuitas.
- "24h sem parar" aqui significa execuções agendadas a cada poucas horas
  (não um processo contínuo em tempo real) — é o modelo que o GitHub
  Actions gratuito permite, e cobre bem o volume de vagas publicadas.
- Se quiser refinar o rating com IA (análise semântica mais precisa via
  API da Claude), dá pra plugar depois — mas isso passa a ter custo por
  chamada, então ficou fora do escopo 100% gratuito.
