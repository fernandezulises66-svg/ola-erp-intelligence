---
doc_id: caja
title: Caja, bancos y cierre diario
category: finanzas
tags: [caja, efectivo, banco, cierre, cobros]
---

# Caja, bancos y cierre diario

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Cuentas de caja

El módulo de caja maneja dos cuentas:

- **Efectivo:** pagos en billetes.
- **Banco:** transferencias, depósitos, cobros con tarjeta y cheques electrónicos (e-cheqs).

Cada cobro o pago genera un **movimiento** con fecha, cuenta, importe, concepto y, si corresponde, la venta, compra o gasto asociado.

## Movimientos automáticos

Cuando se registra el cobro de una venta, el pago de una compra o un gasto pagado, el sistema propone crear el movimiento de caja asociado. El usuario confirma la cuenta (Efectivo o Banco) y el movimiento queda vinculado al comprobante. Si el usuario rechaza la propuesta, el comprobante queda marcado como "sin impacto en caja" y aparece en el reporte de pendientes de conciliación.

## Endpoints

| Método y ruta | Rol mínimo | Descripción |
|---|---|---|
| `GET /api/caja/movimientos?cuenta=&fecha=` | vendedor | Lista los movimientos de una cuenta |
| `POST /api/caja/movimientos` | vendedor | Registra un movimiento manual |
| `POST /api/caja/ajustes` | encargado_local | Movimiento de ajuste entre cuentas (por ejemplo, un cobro cargado en la cuenta equivocada) |
| `POST /api/caja/cierre` | encargado_local | Cierra el día |

## Cierre diario

El cierre lo hace el encargado de local al final de la jornada:

1. Cuenta el efectivo físico y lo envía en el campo `efectivo_contado`.
2. El sistema calcula el saldo teórico de la cuenta Efectivo y la diferencia.
3. Si la diferencia supera los $5.000 (en más o en menos), el campo `justificacion` es obligatorio; si falta, la API responde `422` con el código `CAJA-422-JUST`.
4. Una vez cerrado el día, ningún usuario puede registrar movimientos con esa fecha. Un intento devuelve `409` con el código `CAJA-409-CERR`; las correcciones se hacen con un ajuste en la fecha actual.

El informe de cierre se descarga en PDF desde `GET /api/caja/cierre/:fecha/pdf`.

## Conciliación bancaria

El rol `administracion` importa el extracto bancario en formato CSV desde la pantalla de conciliación. El sistema empareja automáticamente los movimientos por importe y fecha (con una tolerancia de 2 días hábiles) y deja los que no encuentran pareja para revisión manual.
