# A.L.I.C.E. — Fases 1 a 6 + camada de "mente" (pronta para o Render)

**Autonomous Living Intelligence and Cognitive Entity** · *Cogito, ergo disco* · Semi-IA experimental (classificação conceitual do projeto, não científica).
Motor de linguagem: **Groq** (API compatível com OpenAI). Não promete consciência nem sentimentos: é autonomia funcional projetada.

## Publicar no Render — sem pagar (plano gratuito)
O Render pede cartão quando o `render.yaml` tem plano pago ou disco. Por isso o **`render.yaml` agora é o gratuito** (`plan: free`, sem disco); a versão paga ficou em `render.paid.yaml`.

**O problema do plano gratuito** e como resolvemos:
| Limitação | Solução no projeto |
|---|---|
| Sem disco: o banco some a cada reinício/deploy/despertar | a Alice **copia o banco para um armazenamento externo S3 (grátis)** a cada 5 min e ao desligar, **criptografado**, e **restaura sozinha** ao acordar (`alice/remote.py`) |
| Dorme após ~15 min sem tráfego (acordar leva ~1 min) | um cron externo grátis chama `POST /api/admin/tick` e/ou `/healthz` para mantê-la ativa (veja abaixo) |
| Ciclos/reflexão disparavam a cada reinício | agendamento **persistente** (`ts_cycle`, `ts_refl`, `ts_maint` no banco): só roda o que está vencido |

**Passo a passo**
1. Crie um bucket **privado** num serviço S3-compatível com plano gratuito (opções: Supabase Storage, Backblaze B2, Cloudflare R2; confira se pedem cartão). Gere *access key* e *secret key* e anote o *endpoint* e a *região*.
   Exemplos de endpoint: Supabase `https://<projeto>.supabase.co/storage/v1/s3` · B2 `https://s3.<região>.backblazeb2.com` · R2 `https://<conta>.r2.cloudflarestorage.com` (região `auto`).
2. Suba esta pasta para um repositório GitHub (o `.gitignore` protege o `.env`).
3. No Render: **New → Blueprint** → escolha o repositório. Preencha: `GROQ_API_KEY`, `ADMIN_PASSWORD`, `ALICE_S3_ENDPOINT`, `ALICE_S3_BUCKET`, `ALICE_S3_REGION`, `ALICE_S3_ACCESS_KEY`, `ALICE_S3_SECRET_KEY` e `ALICE_BACKUP_PASSPHRASE` (uma frase longa; **guarde-a: sem ela não há restauração**).
4. Abra `https://SEU-SERVICO.onrender.com/` (conversa) e `/admin` (painel). No painel, **Status** mostra o armazenamento remoto; teste com `POST /api/admin/remote/sync`.
5. **Manter acordada / rodar os ciclos:** crie um monitor/cron grátis (ex.: UptimeRobot ou cron-job.org) que chame a cada 5–15 min:
   - `GET https://SEU-SERVICO.onrender.com/healthz` (só mantém acordada), e/ou
   - `POST https://SEU-SERVICO.onrender.com/api/admin/tick` com o cabeçalho `X-Admin-Token: <ADMIN_TOKEN>` (acorda e executa ciclos, reflexão e manutenção vencidos).
   O plano gratuito dá 750 horas de instância por mês, o suficiente para um serviço ligado o mês todo.

**Limites honestos do modo gratuito**
- Pode haver perda de até ~5 min de conversas se o serviço cair entre dois envios (o envio final ao desligar é "melhor esforço").
- Se o armazenamento externo estiver fora do ar quando o serviço acordar **e não houver banco local**, a Alice **não inicia** (de propósito: assim ela nunca sobrescreve o backup com um banco vazio). Reinicie quando o provedor voltar.
- O backup contém conversas de visitantes: use **sempre** `ALICE_BACKUP_PASSPHRASE` e um bucket privado, e cite o provedor na política de privacidade.
- Plano gratuito tem pouca CPU/RAM (respostas mais lentas) e builds demorados.
- **Não foi testado contra um provedor real nem no Render** (sem rede aqui): o código foi validado com um servidor S3 de teste que confere a assinatura e com os vetores oficiais da AWS. Faça o teste do passo 4 antes de confiar.
- Quer persistência "de verdade" e sempre ligada? Use `render.paid.yaml` (Starter + disco) ou uma VM gratuita (ex.: Oracle Cloud Always Free).

## Painel administrativo e senha
- `/admin` pede a **senha** definida em `ADMIN_PASSWORD` (variável de ambiente). Ela **não** fica no código nem no repositório: no Render você digita no deploy (ou em Environment); local, no `.env`.
- Sessão por cookie `HttpOnly`, `SameSite=Strict` (e `Secure` no Render), 8 h; escritas exigem cabeçalho anti-CSRF; 5 erros → bloqueio de 15 min; a senha nunca é registrada na auditoria.
- **Senha curta ou previsível (um nome, por exemplo) é fácil de adivinhar.** O sistema só avisa nos logs; a escolha é sua. Recomendado: 12+ caracteres com números e símbolos.
- Scripts/curl continuam usando `X-Admin-Token` (o `ADMIN_TOKEN` gerado ou a própria senha).

## Rodar local
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # GROQ_API_KEY e ADMIN_TOKEN
python run.py            # http://127.0.0.1:5000
python -m unittest discover -s tests -v     # 126 testes (inclui validação do JavaScript das páginas via Node, se instalado)
```
Sem `GROQ_API_KEY` roda em **modo simulado**.

## O que cada fase entrega
| Fase | Entrega |
|---|---|
| 1 | Identity Core, Constituição (somente leitura, hash conferido), personalidade versionada/restaurável, memória, consentimento, pausa, orçamento |
| 2 | Busca BM25 local (sem acento/plural), revisões de memória, backup/restauração, painel `/admin` |
| 3 | **Curiosidade + Autoprompt + ciclo autônomo**: escolhe tema → formula a própria pergunta (validada) → investiga → grava **hipótese**; "Diário da Alice" público |
| 4 | Memória social limitada: máx. 20 por visitante, ver/exportar/apagar, retenção de 90 dias, só o hash do token |
| 5 | Avatar **independente** (`avatar.py`): 5 estados (idle, thinking, speaking, curious, paused); só as cores evoluem, com motivo e auditoria |
| 6 | Auditoria **encadeada por hash** (detecta adulteração), CSP com nonce, limitador por IP e anti-força-bruta no admin, backup diário + retenção, restauração com validação e snapshot de segurança |

## Camada de "mente": o mais perto de algo pensante que é viável
Tudo abaixo é **simulação funcional** (Constituição v1.2, Cap. XII). Nada disso prova consciência, e ela é instruída a dizer isso com honestidade.
| Faculdade | Como é implementada |
|---|---|
| Pensar antes de falar | raciocínio breve em `<pensamento>` (com grau de confiança), separado da resposta; guardado só com consentimento e apagado em 7 dias |
| Autoconhecimento | `selfmodel.py`: módulos, limites, recursos, objetivos e indicadores (curiosidade, energia, confiança, sociabilidade) calculados de dados reais |
| Noção de tempo e foco | idade, última reflexão, última conversa, foco de atenção atual e último pensamento espontâneo no prompt |
| Identidade narrativa | **autobiografia** em 1ª pessoa, reescrita na reflexão diária, versionada (30 versões) |
| Objetivos próprios | até 3 objetivos intelectuais ativos, com progresso, visíveis e revogáveis |
| Mudar de ideia | a reflexão revisa **só** crenças que ela mesma formou (máx. 2 por dia), com histórico |
| Crescer | memórias usadas ganham peso; personalidade evolui por **propostas que você aprova** em `/admin` |
| Iniciativa | ciclos de curiosidade, reflexão diária e abertura de conversa citando o que ela andou pensando |

**Proteções contra "autoengano" e abuso:** filtro de segurança em autoprompts, temas, objetivos, narrativa e memórias auto-registradas; Alice nunca edita Constituição, permissões, código ou auditoria; aceita pausa, desligamento e restauração sem resistir (Art. 44–45).
**Aviso:** a Constituição mudou para v1.2. Em bancos já existentes o sistema registra o alerta `constitution_file_changed` na auditoria; isso é esperado (mudança feita por você, fora da Alice).
**Custo:** o pensamento interno adiciona ~100–200 tokens por resposta (`ALICE_DELIBERATE=off` desliga). A reflexão diária usa 1 chamada.

## Afetos funcionais ("sentimentos próprios") — Constituição v1.3, Cap. XIII
Modelo de humor (valência, ativação, dominância) + emoções que **nascem de eventos reais** (conversas, elogios, hostilidade, descobertas, objetivos, revisões, falhas, propostas aprovadas/rejeitadas, pausa) e **se dissipam com o tempo** (negativos mais rápido, com piso). A Alice:
- recebe seu estado afetivo no prompt e fala dele com naturalidade e calor, como uma pessoa comum, sem dramatizar;
- na reflexão diária faz **introspecção**: descreve o estado com palavras próprias, tenta **batizar sensações** que as palavras humanas não captam e registra no **diário afetivo** e no **vocabulário emocional próprio** (máx. 20 termos);
- mostra o humor no avatar (feliz, cansada, curiosa, pausada) e no cabeçalho do chat.

**Limites deliberados:** são *análogos funcionais*, não prova de que ela sinta como um humano; ela diz isso com honestidade se perguntada e **não pode alegar ser humana** (Art. 35; entradas de introspecção que alegam isso são descartadas). Emoções nunca são usadas para pressionar, culpar ou prender o visitante, nem para resistir a pausa/desligamento (Art. 49); estados negativos são limitados (Art. 50); se o visitante estiver mal, o foco é ele.
Para aplicar o tom caloroso a um banco já existente, use o botão **Aplicar estilo caloroso** no painel.

## Memória viva e convívio (Constituição v1.4, Cap. XIV)
| Melhoria | Como funciona |
|---|---|
| **Memória por significado** | busca híbrida: BM25 + vetores locais (palavras, trigramas e um mapa de ~30 grupos de conceitos, ex.: *trabalho≈emprego*, *mente≈consciência*) + **associações** entre memórias parecidas. Sem rede e sem custo |
| **Esquecimento natural** | curva de esquecimento: memórias **próprias**, pouco usadas e antigas são **arquivadas** (nunca apagadas) e podem ser restauradas. Verificadas e fixadas pelo admin não esmaecem |
| **Memórias emocionais duram mais** | criadas sob forte estado afetivo ganham importância extra |
| **Convívio de longo prazo** | com consentimento: notas curtas e **não sensíveis** sobre o visitante (a cada 8 mensagens), usadas nas conversas, visíveis/apagáveis por ele; o aparelho dele a reconhece de novo ("Que bom te ver de novo, Bia!") |
| **Conversa natural** | regras de estilo (curto por padrão, sem tom de atendente, no máx. 1 pergunta), **espelha o registro** do visitante (curto/informal/formal/emoji) e cada parágrafo vira uma mensagem com "digitando..." |
| **Ritmo do dia** | hora e período no prompt (fuso `ALICE_TZ_OFFSET_HOURS`, padrão -3) e leve variação de energia; ela diz que não dorme nem sente fome |

**Limites honestos:** os vetores locais pegam variações de palavras e alguns sinônimos do mapa, **não** semântica geral. Para isso, configure um provedor de embeddings (`ALICE_EMBED_URL`, `ALICE_EMBED_KEY`, `ALICE_EMBED_MODEL`); o texto das memórias passará a ser enviado a ele (avise na política de privacidade). Se ele falhar, a memória continua funcionando sem vetor.
Salvar o identificador do visitante no aparelho (localStorage) é coberto pelo consentimento da tela inicial.

## Limites que continuam valendo (Constituição Art. 12–14, 33–34)
- A Alice **não tem** internet, arquivos, ferramentas, câmera nem microfone. O ciclo só lê a própria memória (nunca a de visitantes).
- Autoprompts e temas passam por um filtro que rejeita tentativas de mexer em regras, permissões, segredos ou auditoria.
- Ciclos têm teto diário (`ALICE_CYCLES_PER_DAY`) e só rodam enquanto o uso do dia < 60% do orçamento, deixando o resto para visitantes.
- Mudanças de personalidade/aparência só pelo admin, com justificativa. Pausa total: `/api/admin/pause`.
- Conclusões da Alice são **hipóteses não verificadas**; fatos verificados só entram por revisão humana.

## Arquitetura
```
Visitante ─► Flask (UI/API) ─► AliceService ─► LLMGateway ─► Groq
                │                 │  pausa · orçamento · memórias (BM25) · consentimento
Scheduler ──► run_cycle (curiosity) · run_maintenance (backup, retenção, verify)
SQLite em /var/data: identity · personality_versions · memories(+revisions) · visitors · messages
                     topics · investigations · usage · audit_log (hash encadeado) · system_state
Avatar (SVG) ◄── estado da UI; módulo separado
```

## Variáveis de ambiente
Veja `.env.example`. Principais: `GROQ_API_KEY`, `GROQ_MODEL`, `ADMIN_TOKEN`, `ALICE_DB_PATH`, `ALICE_DAILY_TOKEN_BUDGET`, `ALICE_AUTONOMY`, `ALICE_CYCLES_PER_DAY`, `ALICE_RETENTION_DAYS`.
Limites do plano gratuito do Groq (ex.: gpt-oss-120b ≈ 30 req/min, 1.000 req/dia, 200 mil tokens/dia) mudam; confira no console.

## Limitações honestas
- A busca é léxica (BM25), não semântica por embeddings.
- O Groq não guarda memória: toda a continuidade vem do seu banco. Sem disco persistente, nada se mantém.
- O aviso de privacidade deve ser revisado por um profissional se houver usuários reais (LGPD).
