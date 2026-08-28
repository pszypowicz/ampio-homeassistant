# Ampio Designer quirks

## Matter device-type tags that only half exist

The device-type tag on an output ("Description in device" -> for example "Lighting - On-off light") is stored twice: in the output's description record inside the module's CAN memory, and mirrored into the `type` column of the M-SERV's object catalogue. The integration classifies relays from the catalogue column, because the column is served to every account tier. The CAN records answer the admin login only, and an entity's platform must build identically on both tiers (see the stability contract below).

Tags saved with older Ampio tooling exist only in the CAN record, and the catalogue column stayed empty. A relay in that state shows its Lighting tag in Designer, yet Home Assistant surfaces it as a switch. To check what the integration sees for an output, download the diagnostics and look up the object's `type` field in the catalogue payload - [debugging.md](debugging.md) shows how.

The fix, in the current web Designer: touch every affected output individually - select a different device type and switch it back to Lighting, so Designer registers an edit - then save once. One save covers all the outputs you touched. Verified behavior on a real install, and confirmed in the Designer web bundle:

- Designer tracks changes per module and per category (a `descriptions` dirty flag on the device), and the save re-sends a dirty module's whole description table over the CAN bus.
- The catalogue column, however, updates only for the outputs you actually edited in the UI. An untouched neighbor keeps its stale column even though its record just went over the wire again - which is why every output needs its own flip, however correct it looks in Designer.
- Designer registers an edit only on a real change, so flip the value away and back. A Lokalizacja change counts too, and the tag rides along with it.

## The stability contract

Ampio accounts upgrade and downgrade between the admin login and app-created users. The integration therefore derives everything that defines an entity's platform or the device topology from data the restricted tier receives.

Entity ids are exempt from that rule, because the integration writes them itself. Home Assistant normally builds an entity id from the area name, the device name, and the entity name. An Ampio entity carries its own id instead, `<domain>.ampio_<server mac>_obj_<object id>`, which is the same string as its unique id. No name reaches it. Rename a device, move it to another area, or switch the account tier, and every id holds still.

That frees the device name. A module takes the name you gave it in Ampio Designer where the admin-only module catalogue answers, and falls back to `Ampio module 0x<MAC>` on a restricted account. The hub is always `M-SERV`. The catalogue also decorates the model, the firmware and hardware versions, and the serial number. All of those follow the account tier, so a tier change renames a device in the interface and moves nothing else.
