# Adrobot interface

## Direction

Product: an operator's workspace for creating Keitaro campaigns and distributing traffic between offers.
Character: exact, calm, practical.
World: advertising placement schedules; legible rows, clear allocations and a persistent distinction between draft and publication.
Moment: the actual traffic split, drawn as one segmented line; pinned allocations are darker.
Signature: the same offer percentages remain aligned in the distribution line and table; a warm header marks unpublished changes.

## Tokens

Light working surface for reading and editing during an ordinary office day. Deep blue header `#183e56`, page `#f3f6f8`, sidebar `#eaf0f4`, white content. Primary action `#17618c`, ink `#203441`, supporting text `#566c7a`, border `#d7e0e6`. Draft surface `#fff6d9` with text `#79550c`. Error text `#a43137`.

System sans for Russian UI, tabular figures and a system mono for IDs. Type scale: 11,12,13,14,16,18,22,26px; weights 400,500,600. Controls at least 40px, primary controls 44px. Base spacing 4px. Control radius8px; stream radius12px. No decorative illustration or entrance animation.

## Structure and behavior

Desktop uses a 300px campaign sidebar and a fluid editor. Below760px the sidebar becomes a short campaign list above the editor. Creation is an inline workspace. Stream controls remain native buttons and inputs. Draft/published state is written in text; archived rows also show a text label and restore action. Every focus target has a visible ring. Offer lookup supports typing, arrow keys, Enter and Escape; stale searches are aborted. Backend calculations are the sole authority for weights.

## Scope

Operate mode, code-first. Product truth comes from the supplied assignment and reference video. No invented performance metrics, charts or claims. Design research informed density and accessibility; catalog marketing patterns were not applied to this application.
