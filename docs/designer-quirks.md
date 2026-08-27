# Ampio Designer quirks

## Matter device-type tags that only half exist

The device-type tag on an output ("Description in device" -> for example "Lighting - On-off light") is stored twice: in the output's description record inside the module's CAN memory, and mirrored into the `type` column of the M-SERV's object catalogue. The integration classifies relays from the catalogue column, because the column is served to every account tier. The CAN records answer the admin login only, and an entity's platform must build identically on both tiers (see the stability contract below).

Tags saved with older Ampio tooling exist only in the CAN record, and the catalogue column stayed empty. A relay in that state shows its Lighting tag in Designer, yet Home Assistant surfaces it as a switch.

The fix is one save per output, in the current web Designer: open the output's "Description in device" panel and save it again. Designer then rewrites the CAN record and fills the catalogue column in the same save. Verified behavior on a real install:

- Every output needs its own save. A save on one output does not sync its neighbors, however correct they look in the Designer UI.
- Designer only saves when something changed, so flip a field back and forth when needed. A Lokalizacja change counts, and the tag rides along with it.

## The stability contract

Ampio accounts upgrade and downgrade between the admin login and app-created users. The integration therefore derives everything that defines an entity's platform or the device topology from data the restricted tier receives. Admin-only surfaces - the module catalogue and the Designer description records - only decorate: module names and versions, suggested areas, Designer locations. Whichever account you configure, and however its tier changes later, the entity set and the device tree stay the same.
