# RadarIA

RadarIA é um projeto do Ecossistema DEV para capturar, filtrar e transformar novidades relevantes de Inteligência Artificial em aprendizado aplicável ao trabalho e aos projetos, reduzindo o excesso de informação e destacando apenas o que merece estudo ou teste.

## Estado inicial

Projeto criado com estrutura mínima. Tecnologia, arquitetura e comandos de
execução ainda não foram definidos.

## Documentação

O conhecimento permanente do projeto deve ser registrado em `docs/` conforme
surgirem necessidades reais.

## Coleta resiliente

Execute `python radaria.py --json` para obter a próxima publicação. Falhas
transitórias recebem até três tentativas e cada fonte é coletada isoladamente.
Uma fonte indisponível aparece em `warnings` sem descartar resultados válidos
das demais fontes.

Os estados `no_new_publications_with_warnings`, `publication_deferred` e
`collection_deferred` representam conclusões parciais que preservam o trabalho
já persistido. `collection_failed` indica indisponibilidade de todas as fontes.
Resultados íntegros já salvos podem ser publicados mesmo quando há avisos; erros
de persistência, validação ou geração da saída continuam bloqueando a publicação.
