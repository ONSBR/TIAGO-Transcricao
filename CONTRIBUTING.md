# Como contribuir

Obrigado pelo interesse em contribuir com o **ONS Transcribe**. Este projeto
foi desenvolvido pelo ONS (Operador Nacional do Sistema Elétrico) para transcrever e
enriquecer as gravações de comunicação operativa do Sistema Interligado Nacional, e é
mantido pela Comunidade de IA do ONS.

Contribuições de qualquer natureza são bem-vindas: correção de defeito, melhoria de
documentação, novo recurso, teste, ou simplesmente relatar um problema que você encontrou.

## Contatos técnicos

| Canal | Endereço |
|---|---|
| **Comunidade de IA ONS** (canal principal) | cia@ons.org.br |
| Mantenedores | Julia Herz (Líder da comunidade) · Rodrigo Ramos Guimarães  (Arquiteto de IA)|
| Discussão pública | [Issues do repositório](https://github.com/ONSBR/TIAGO-Transcricao/issues) |

Para dúvidas de arquitetura, decisões de produto ou pedidos de acesso, escreva para
**cia@ons.org.br**. Para defeitos e sugestões, prefira abrir uma issue — assim a discussão
fica registrada para os próximos contribuidores.

## Antes de começar

> [!IMPORTANT]
> **Este é um repositório público.** Nenhum dado operativo real do Sistema Interligado
> Nacional pode ser versionado aqui. Leia a seção
> [Regras de segurança](#regras-de-segurança) antes do primeiro commit.

Para colocar o ambiente de pé, siga o [README](README.md#instalação-e-configuração). Ele
cobre os pré-requisitos de cada módulo e como preencher os arquivos de configuração a
partir dos modelos `.example` / `.sample`.

## Regras de segurança

Estas regras não são negociáveis. Um pull request que as viole será fechado, e o dado
exposto precisará ser tratado como incidente.

**Nunca versione:**

- Áudios (`.wav`, `.mp3`) ou transcrições de comunicação operativa real, ainda que em
  trecho, exemplo ou teste.
- Nomes de operadores do ONS, de agentes do setor elétrico, ou qualquer dado que permita
  identificar uma pessoa.
- Credenciais de qualquer tipo: chave de acesso AWS, token, senha, URL pré-assinada de S3,
  arquivo `.pem`/`.pfx`.
- Identificadores de infraestrutura real: número de conta AWS, ARN completo, id de VPC,
  subnet, security group, endpoint de OpenSearch, DNS interno, nome real de bucket.
- Estado ou plano do Terraform (`terraform.tfstate`, `*.tfplan`), e o
  `terraform.tfvars` preenchido.
- Prompts de domínio preenchidos (`ONSTranscribe/api/terraform/prompts/*.txt`) e as
  correções de texto específicas do ambiente
  (`ONSTranscribe/api/app/utils/text_corrections.py`).

**O padrão do repositório é o arquivo de exemplo.** Cada configuração sensível tem um par:
o arquivo real fica no `.gitignore`, e um modelo versionado documenta o formato.

| Arquivo real (ignorado) | Modelo versionado |
|---|---|
| `ONSTranscribe/api/terraform/terraform.tfvars` | `terraform.tfvars.sample` |
| `ONSTranscribe/api/terraform/prompts/*.txt` | `prompts/*.example.txt` |
| `ONSTranscribe/api/app/utils/text_corrections.py` | `text_corrections_example.py` |
| `ONSTranscribe/scripts/Montagem_Ambiente/.env` | `.env.sample` |
| `ONSTranscribe/api/.env` | documentado no README |

Ao introduzir uma nova configuração, crie o par e registre o arquivo real no `.gitignore`
no mesmo commit.

**Antes de abrir o PR**, rode a varredura local:

```bash
# nada de chave de acesso, conta AWS, ARN com conta ou id de rede
git diff --cached | grep -nE 'AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|[0-9]{12}|arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]{12}|vpc-[0-9a-f]{8}|subnet-[0-9a-f]{8}|sg-[0-9a-f]{8}'

# nada de credencial ou chave privada
git diff --cached | grep -nE 'aws_secret_access_key|BEGIN [A-Z ]*PRIVATE KEY|password\s*[:=]|senha\s*[:=]'
```

Se algum dos comandos retornar linha, corrija antes de seguir.

## Fluxo de contribuição

1. **Abra uma issue** descrevendo o problema ou a proposta, antes de escrever código. Para
   mudanças grandes, isso evita trabalho perdido.
2. **Crie um branch** a partir de `main`, com nome descritivo:
   - `fix/descricao-curta` — correção de defeito
   - `feature/descricao-curta` — novo recurso
   - `docs/descricao-curta` — documentação
   - `infra/descricao-curta` — infraestrutura e Terraform
3. **Faça commits pequenos e coesos**, seguindo a convenção do projeto (abaixo).
4. **Abra o pull request** para `main`, preenchendo o [checklist](#checklist-do-pull-request).
5. **Responda à revisão.** Todo PR precisa de aprovação de um mantenedor.

### Convenção de mensagens de commit

O projeto usa uma variação de [Conventional Commits](https://www.conventionalcommits.org/):

```
<tipo>(<escopo opcional>): <resumo no imperativo, minúsculas, sem ponto final>

<corpo opcional: o porquê da mudança, não o quê>
```

Tipos em uso, na frequência real do repositório:

| Tipo | Quando usar |
|---|---|
| `fix` | Corrige comportamento defeituoso |
| `add` | Acrescenta funcionalidade ou artefato novo |
| `chore` | Manutenção que não altera comportamento (dependências, arquivos de apoio) |
| `infra` | Mudança de infraestrutura ou Terraform |
| `docs` | Só documentação |
| `refactor` | Reorganiza código sem mudar comportamento |
| `test` | Acrescenta ou ajusta teste |
| `remove` | Retira código, recurso ou artefato |
| `tune` | Ajuste de parâmetro operacional (limite, timeout, threshold) |

Exemplos reais do histórico:

```
fix(transcribe): não mascarar OOM de segmento como diarização vazia
add(worker): worker SQS desacoplado que consome a fila e transcreve, sequencial
infra(transcribe): capacity provider Managed Instances para GPU
tune(infra): threshold do scale-up 5 -> 15 (pelo backlog real de prod)
```

Escreva o resumo em português, no imperativo. Se o commit resolve uma issue, cite no
corpo: `Resolve #42`.

## Padrões de código

O repositório tem duas stacks: Python, para a API e as funções Lambda, e Terraform, para
a infraestrutura.

### Python — API, worker e Lambdas

- Gerenciador de dependências: [Astral UV](https://docs.astral.sh/uv/) (`uv sync`, `uv add`)
- Type hints em toda função pública; docstrings nos módulos e serviços
- Pydantic para validação de entrada e saída
- `async`/`await` para I/O. O trabalho de GPU é **sequencial por decisão de projeto** —
  paralelizar segmentos aumenta o pico de VRAM e causa `CUDA OOM`. Veja
  `MAX_TRANSCRIBE_WORKERS` em `app/config.py` antes de propor concorrência ali.
- Configuração só por variável de ambiente, lida em `app/config.py` — não espalhe
  `os.getenv` pelo código
- Os clientes AWS são construídos **sem credencial explícita**, deixando o SDK resolver
  pela cadeia padrão. Não introduza access key em nenhum caminho.

```bash
cd ONSTranscribe/api && uv sync && uv run pytest
cd ONSTranscribe/api/terraform/lambdas && python3 -m pytest tests
```

### Terraform

- Nenhum valor real de infraestrutura no `.tf`: use `variable` e documente no
  `terraform.tfvars.sample`
- Nomes de recurso parametrizados por `${var.project_name}` e `${var.environment}`
- Prompts do Bedrock ficam em arquivo, carregados com `file()` — não em heredoc inline
- Ao acrescentar uma `variable` sem `default`, inclua a entrada correspondente no `.sample`
- Policies IAM com o mínimo necessário: enumere as ações que o código chama, em vez de
  anexar uma policy gerenciada ampla
- Rode `terraform fmt -recursive` antes do commit

```bash
terraform -chdir=ONSTranscribe/api/terraform fmt -recursive
terraform -chdir=ONSTranscribe/api/terraform validate
```

## Checklist do pull request

- [ ] A varredura de segredos da seção [Regras de segurança](#regras-de-segurança) não
      retornou nada
- [ ] Nenhum áudio, transcrição ou nome de pessoa real foi adicionado
- [ ] Toda configuração nova tem par `.example`/`.sample` e entrada no `.gitignore`
- [ ] Os testes passam (`uv run pytest` na API, `pytest tests` nas lambdas)
- [ ] `terraform fmt -check -recursive` e `terraform validate` passam, se houve mudança
      de infraestrutura
- [ ] A documentação afetada foi atualizada (README, guias de deploy)
- [ ] As mensagens de commit seguem a convenção
- [ ] O PR descreve **o que** mudou e **por quê**

## Relatando um problema

Ao abrir uma issue, inclua:

- **O que aconteceu** e **o que você esperava**
- **Componente afetado** (API/worker, Lambdas, Terraform, finetuning, métricas)
- **Como reproduzir**, no menor caminho possível
- **Logs e mensagens de erro** — com os dados sensíveis removidos
- **Versões**: Python, Terraform, região AWS, tipo de instância de GPU

Para uma **vulnerabilidade de segurança**, não abra issue pública: escreva diretamente para
**cia@ons.org.br** descrevendo o problema e o impacto.

## Código de conduta

Trate as pessoas com respeito. Critique o código, não quem o escreveu. Não são tolerados
assédio, ataque pessoal ou linguagem discriminatória. Casos podem ser reportados a
**cia@ons.org.br**.

## Licença das contribuições

Ao contribuir, você concorda que sua contribuição será licenciada sob a
[Apache License 2.0](LICENSE), a mesma licença do projeto.
